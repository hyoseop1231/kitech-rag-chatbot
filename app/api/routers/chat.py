from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json

from app.config import settings
from app.utils.logging_config import get_logger
from app.utils.security import sanitize_input, validate_document_id
from app.utils.query_validator import QueryValidator
from app.services.text_processing_service import get_embeddings
from app.services.vector_db_service import search_multimodal_content
from app.services.streaming_service import process_multimodal_llm_chat_request_stream
from app.services.multimodal_llm_service import process_multimodal_llm_chat_request, enhance_response_with_media_references
from app.services.fallback_response_service import FallbackResponseService
from app.utils.exceptions import EmbeddingError, VectorDBError, LLMError

logger = get_logger(__name__)
router = APIRouter()

# Pydantic models for chat requests and responses
class ChatRequest(BaseModel):
    query: str
    document_id: Optional[str] = None
    document_ids: Optional[List[str]] = None
    model_name: Optional[str] = settings.OLLAMA_DEFAULT_MODEL
    lang: Optional[str] = "ko"
    conversation_history: Optional[List[Dict[str, str]]] = None

class ChatResponse(BaseModel):
    query: str
    response: str
    source_document_id: Optional[str] = None
    retrieved_chunks_preview: Optional[List[str]] = None
    content_summary: Optional[Dict[str, int]] = None
    media_references: Optional[Dict[str, Any]] = None


@router.post("/chat/stream/")
async def chat_with_documents_stream(request: ChatRequest):
    """
    Processes a chat query with a streaming response for a real-time user experience.
    """
    query = request.query
    model_name = request.model_name
    lang = request.lang or "ko"
    conversation_history = request.conversation_history or []
    document_ids = request.document_ids or ([request.document_id] if request.document_id else [])

    async def stream_response():
        try:
            yield f"data: {json.dumps({'type': 'status', 'message': 'Analyzing query...'}, ensure_ascii=False)}\n\n"

            validation_result = QueryValidator.validate_query(query)
            if not validation_result['is_valid']:
                error_response = {'type': 'validation_error', 'message': validation_result['suggestion'], 'suggestions': QueryValidator.get_query_suggestions(query)}
                yield f"data: {json.dumps(error_response, ensure_ascii=False)}\n\n"
                return

            enhanced_query = QueryValidator.enhance_query_for_search(query)
            query_embeddings = get_embeddings([enhanced_query])
            if not query_embeddings:
                raise EmbeddingError("Failed to generate embeddings for the query.")

            yield f"data: {json.dumps({'type': 'status', 'message': 'Searching documents...'}, ensure_ascii=False)}\n\n"

            filter_metadata = {"source_document_id": {"$in": document_ids}} if document_ids else None
            multimodal_results = search_multimodal_content(
                query_vector=query_embeddings[0], top_k=settings.TOP_K_RESULTS, filter_metadata=filter_metadata,
                include_images=True, include_tables=True
            )

            retrieved_chunks = multimodal_results.get('text', [])
            retrieved_images = multimodal_results.get('images', [])
            retrieved_tables = multimodal_results.get('tables', [])

            if not retrieved_chunks and not retrieved_images and not retrieved_tables:
                fallback_data = FallbackResponseService.generate_no_results_response(query, document_ids)
                yield f"data: {json.dumps({'type': 'no_results', 'message': fallback_data['response'], 'suggestions': fallback_data['suggestions']}, ensure_ascii=False)}\n\n"
                return

            yield f"data: {json.dumps({'type': 'status', 'message': 'Generating response...'}, ensure_ascii=False)}\n\n"

            llm_options = {"temperature": settings.LLM_TEMPERATURE, "top_p": 0.9}
            stream_generator = process_multimodal_llm_chat_request_stream(
                user_query=query, multimodal_content=multimodal_results, model_name=model_name,
                lang=lang, options=llm_options, conversation_history=conversation_history
            )

            full_response = ""
            for chunk in stream_generator:
                full_response += chunk
                yield f"data: {json.dumps({'type': 'content', 'content': chunk}, ensure_ascii=False)}\n\n"

            enhanced_response = enhance_response_with_media_references(full_response, retrieved_images, retrieved_tables)

            final_data = {
                "type": "final",
                "metadata": {
                    'content_summary': {'text_chunks': len(retrieved_chunks), 'images': len(retrieved_images), 'tables': len(retrieved_tables)},
                    'media_references': enhanced_response.get('referenced_media', {})
                }
            }
            yield f"data: {json.dumps(final_data, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"Streaming chat error: {e}", exc_info=True)
            fallback_message = FallbackResponseService.generate_error_response("system_error", str(e), query)
            yield f"data: {json.dumps({'type': 'error', 'message': fallback_message}, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat/", response_model=ChatResponse)
async def chat_with_llm(chat_request: ChatRequest):
    """
    Handles non-streaming chat requests.
    """
    query = sanitize_input(chat_request.query, max_length=2000)
    if not query:
        raise HTTPException(status_code=400, detail="Query is required.")

    validation_result = QueryValidator.validate_query(query)
    if not validation_result['is_valid']:
        raise HTTPException(status_code=400, detail={"message": validation_result['suggestion'], "suggestions": QueryValidator.get_query_suggestions(query)})

    document_ids = chat_request.document_ids or ([chat_request.document_id] if chat_request.document_id else [])
    for doc_id in document_ids:
        if not validate_document_id(doc_id):
            raise HTTPException(status_code=400, detail=f"Invalid document ID: {doc_id}")

    try:
        enhanced_query = QueryValidator.enhance_query_for_search(query)
        query_embedding = get_embeddings([enhanced_query])[0]

        filter_metadata = {"source_document_id": {"$in": document_ids}} if document_ids else None
        multimodal_results = search_multimodal_content(
            query_vector=query_embedding, top_k=5, filter_metadata=filter_metadata,
            include_images=True, include_tables=True
        )

        retrieved_chunks = multimodal_results.get('text', [])
        retrieved_images = multimodal_results.get('images', [])
        retrieved_tables = multimodal_results.get('tables', [])

        if not retrieved_chunks and not retrieved_images and not retrieved_tables:
            fallback_data = FallbackResponseService.generate_no_results_response(query, document_ids)
            return ChatResponse(query=query, response=fallback_data['response'])

        llm_options = {"temperature": settings.LLM_TEMPERATURE, "top_p": 0.9}
        llm_response_text = process_multimodal_llm_chat_request(
            user_query=query, multimodal_content=multimodal_results, model_name=chat_request.model_name,
            lang=chat_request.lang, options=llm_options, conversation_history=chat_request.conversation_history
        )

        enhanced_response = enhance_response_with_media_references(llm_response_text, retrieved_images, retrieved_tables)

        return ChatResponse(
            query=query,
            response=enhanced_response.get('text', llm_response_text),
            source_document_id=document_ids[0] if document_ids else None,
            retrieved_chunks_preview=[c.get('text', '')[:100] for c in retrieved_chunks],
            content_summary={'text_chunks': len(retrieved_chunks), 'images': len(retrieved_images), 'tables': len(retrieved_tables)},
            media_references=enhanced_response.get('referenced_media', {})
        )

    except (EmbeddingError, VectorDBError, LLMError) as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")
