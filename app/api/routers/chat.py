"""
Chat router — 채팅 엔드포인트 (일반 + 스트리밍)
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
import time

from app.config import settings
from app.utils.logging_config import get_logger
from app.utils.security import sanitize_input, validate_document_id
from app.utils.query_validator import QueryValidator
from app.services.text_processing_service import get_embeddings
from app.services.vector_db_service import search_multimodal_content
from app.services.multimodal_llm_service import process_multimodal_llm_chat_request, enhance_response_with_media_references
from app.services.streaming_service import process_multimodal_llm_chat_request_stream
from app.services.fallback_response_service import FallbackResponseService
from app.utils.exceptions import EmbeddingError, VectorDBError, LLMError

logger = get_logger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    query: str
    document_id: Optional[str] = None
    document_ids: Optional[List[str]] = None
    model_name: Optional[str] = None  # Use provider's default if not specified
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
    Process a chat query with streaming response for real-time user experience.
    """
    query = request.query
    # Use appropriate default model based on provider
    if request.model_name:
        model_name = request.model_name
    elif settings.LLM_PROVIDER == "openrouter":
        model_name = settings.OPENROUTER_DEFAULT_MODEL
    else:
        model_name = settings.OLLAMA_DEFAULT_MODEL
    lang = request.lang or "ko"
    conversation_history = request.conversation_history or []
    logger.info(f"Stream request - conversation history: {len(conversation_history)} messages")
    
    document_ids = []
    if request.document_ids:
        document_ids = request.document_ids
    elif request.document_id:
        document_ids = [request.document_id]

    async def stream_response():
        try:
            # 1. Query embedding
            yield f"data: {json.dumps({'type': 'status', 'message': '질문 분석 중...'}, ensure_ascii=False)}\n\n"
            
            start_time = time.time()
            
            validation_result = QueryValidator.validate_query(query)
            if not validation_result['is_valid']:
                error_response = {
                    'type': 'validation_error',
                    'message': validation_result['suggestion'],
                    'suggestions': QueryValidator.get_query_suggestions(query)
                }
                yield f"data: {json.dumps(error_response, ensure_ascii=False)}\n\n"
                return
            
            try:
                enhanced_query = QueryValidator.enhance_query_for_search(query)
                query_embeddings = get_embeddings([enhanced_query])
                if not query_embeddings:
                    fallback_data = FallbackResponseService.generate_error_response(
                        "embedding_error", "임베딩 생성 실패", query
                    )
                    yield f"data: {json.dumps({'type': 'error', 'message': fallback_data}, ensure_ascii=False)}\n\n"
                    return
                query_vector = query_embeddings[0]
                embedding_time = time.time() - start_time
                logger.info(f"Query embedding generation took: {embedding_time:.2f} seconds")
            except Exception as e:
                logger.error(f"임베딩 생성 실패: {e}")
                fallback_data = FallbackResponseService.generate_error_response(
                    "embedding_error", str(e), query
                )
                yield f"data: {json.dumps({'type': 'error', 'message': fallback_data}, ensure_ascii=False)}\n\n"
                return
            
            # 2. Vector search
            yield f"data: {json.dumps({'type': 'status', 'message': '관련 문서 검색 중...'}, ensure_ascii=False)}\n\n"
            
            try:
                filter_metadata = None
                if document_ids and len(document_ids) > 0:
                    if len(document_ids) == 1:
                        filter_metadata = {"source_document_id": document_ids[0]}
                    else:
                        filter_metadata = {"source_document_id": {"$in": document_ids}}
                
                multimodal_results = search_multimodal_content(
                    query_vector=query_vector,
                    top_k=settings.TOP_K_RESULTS,
                    filter_metadata=filter_metadata,
                    include_images=True,
                    include_tables=True
                )
            except Exception as e:
                logger.error(f"벡터 검색 실패: {e}")
                yield f"data: {json.dumps({'type': 'error', 'message': f'문서 검색 중 오류가 발생했습니다: {str(e)}'}, ensure_ascii=False)}\n\n"
                return
            
            retrieved_chunks = multimodal_results.get('text', [])
            retrieved_images = multimodal_results.get('images', [])
            retrieved_tables = multimodal_results.get('tables', [])
            
            logger.info(f"Streaming search results: text={len(retrieved_chunks)}, images={len(retrieved_images)}, tables={len(retrieved_tables)}")
            
            if not retrieved_chunks and not retrieved_images and not retrieved_tables:
                fallback_data = FallbackResponseService.generate_no_results_response(query, document_ids)
                yield f"data: {json.dumps({'type': 'no_results', 'message': fallback_data['response'], 'suggestions': fallback_data['suggestions']}, ensure_ascii=False)}\n\n"
                return
            
            if retrieved_chunks:
                logger.info(f"First text chunk: {retrieved_chunks[0].get('text', '')[:100]}...")
            if retrieved_tables:
                logger.info(f"First table content: {retrieved_tables[0].get('content', '')[:100]}...")
            
            # 3. Stream LLM response
            yield f"data: {json.dumps({'type': 'status', 'message': '답변 생성 중...'}, ensure_ascii=False)}\n\n"
            
            all_retrieved_content = {
                'text': retrieved_chunks,
                'images': retrieved_images,
                'tables': retrieved_tables
            }
            
            llm_options = {
                "num_predict": settings.LLM_NUM_PREDICT_MULTIMODAL,
                "temperature": settings.LLM_TEMPERATURE,
                "top_p": 0.9,
                "repeat_penalty": 1.1
            }
            
            full_response = ""
            word_buffer = ""
            
            try:
                stream_generator = process_multimodal_llm_chat_request_stream(
                    user_query=query,
                    multimodal_content=all_retrieved_content,
                    model_name=model_name,
                    lang=lang,
                    options=llm_options,
                    conversation_history=conversation_history
                )
            except Exception as e:
                logger.error(f"LLM 스트리밍 초기화 실패: {e}")
                yield f"data: {json.dumps({'type': 'error', 'message': f'답변 생성 초기화 중 오류가 발생했습니다: {str(e)}'}, ensure_ascii=False)}\n\n"
                return
            
            for chunk in stream_generator:
                full_response += chunk
                word_buffer += chunk
                
                korean_delimiters = [' ', '\n', '.', ',', '!', '?', ')', ']', '}', '다.', '다,', '다!', '다?', '요.', '요,', '요!', '요?']
                if any(delimiter in word_buffer for delimiter in korean_delimiters) or len(word_buffer) > 50:
                    chunk_data = {
                        "type": "content",
                        "content": word_buffer,
                        "is_final": False
                    }
                    yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                    word_buffer = ""
            
            if word_buffer:
                chunk_data = {
                    "type": "content",
                    "content": word_buffer,
                    "is_final": False
                }
                yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
            
            enhanced_response = enhance_response_with_media_references(
                full_response,
                retrieved_images,
                retrieved_tables
            )
            
            final_response_text = enhanced_response.get('text', full_response) if enhanced_response else full_response
            if not final_response_text or not final_response_text.strip():
                fallback_data = FallbackResponseService.generate_no_results_response(query, document_ids)
                final_response_text = fallback_data['response']
            elif len(final_response_text.strip()) < 50:
                total_results = len(retrieved_chunks) + len(retrieved_images) + len(retrieved_tables)
                final_response_text = FallbackResponseService.enhance_poor_results_response(
                    final_response_text, query, total_results
                )
            
            final_data = {
                "type": "final",
                "metadata": {
                    "content_summary": {
                        'text_chunks': len(retrieved_chunks),
                        'images': len(retrieved_images),
                        'tables': len(retrieved_tables)
                    },
                    "media_references": {
                        'images': enhanced_response.get('referenced_images', []) if enhanced_response else [],
                        'tables': enhanced_response.get('referenced_tables', []) if enhanced_response else [],
                        'has_media': enhanced_response.get('has_media', False) if enhanced_response else False
                    },
                }
            }
            yield f"data: {json.dumps(final_data, ensure_ascii=False)}\n\n"
            
        except Exception as e:
            logger.error(f"Streaming chat error: {e}", exc_info=True)
            fallback_message = FallbackResponseService.generate_error_response(
                "system_error", str(e), query
            )
            error_data = {
                "type": "error",
                "message": fallback_message
            }
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/chat/", response_model=ChatResponse)
async def chat_with_llm(chat_request: ChatRequest):
    """
    Handles chat requests with input validation and security checks.
    """
    query = sanitize_input(chat_request.query, max_length=2000)
    if not query:
        raise HTTPException(status_code=400, detail="Query not provided or invalid.")
    
    conversation_history = chat_request.conversation_history or []
    logger.info(f"Received conversation history: {len(conversation_history)} messages")
    
    validation_result = QueryValidator.validate_query(query)
    if not validation_result['is_valid']:
        suggestions = QueryValidator.get_query_suggestions(query)
        raise HTTPException(
            status_code=400, 
            detail={
                "message": validation_result['suggestion'],
                "error_type": validation_result['error_type'],
                "suggestions": suggestions
            }
        )
    
    document_ids = chat_request.document_ids or ([] if chat_request.document_id is None else [chat_request.document_id])
    if document_ids:
        for doc_id in document_ids:
            if not validate_document_id(doc_id):
                raise HTTPException(status_code=400, detail=f"Invalid document ID: {doc_id}")
    
    # Use appropriate default model based on provider
    if chat_request.model_name:
        model_name = chat_request.model_name
    elif settings.LLM_PROVIDER == "openrouter":
        model_name = settings.OPENROUTER_DEFAULT_MODEL
    else:
        model_name = settings.OLLAMA_DEFAULT_MODEL
    lang = chat_request.lang or "ko"

    logger.info(f"Chat request: Query length={len(query)}, DocIDs={len(document_ids)}, Model={model_name}, Lang={lang}")
    logger.debug(f"Query preview: {query[:100]}...")

    try:
        logger.info("Step 1: Embedding user query...")
        enhanced_query = QueryValidator.enhance_query_for_search(query)
        query_embedding_list = get_embeddings([enhanced_query])
        if not query_embedding_list or not query_embedding_list[0]:
            raise EmbeddingError("Could not generate embedding for the query", "QUERY_EMBEDDING_FAILED")
        query_embedding = query_embedding_list[0]

        logger.info("Step 2: Searching multimodal content for relevant information...")
        filter_metadata = None
        if document_ids and len(document_ids) > 0:
            if len(document_ids) == 1:
                filter_metadata = {"source_document_id": document_ids[0]}
            else:
                filter_metadata = {"source_document_id": {"$in": document_ids}}
            logger.debug(f"Applying filter: {filter_metadata}")
        
        multimodal_results = search_multimodal_content(
            query_vector=query_embedding, 
            top_k=5, 
            filter_metadata=filter_metadata,
            include_images=True,
            include_tables=True
        )
        
        retrieved_chunks = multimodal_results.get('text', [])
        retrieved_images = multimodal_results.get('images', [])
        retrieved_tables = multimodal_results.get('tables', [])

        if not any([retrieved_chunks, retrieved_images, retrieved_tables]):
            logger.warning("No relevant content found in Vector DB")
            fallback_data = FallbackResponseService.generate_no_results_response(query, document_ids)
            return ChatResponse(
                query=query,
                response=fallback_data['response'],
                source_document_id=document_ids[0] if document_ids else None,
                retrieved_chunks_preview=[],
                content_summary={'text_chunks': 0, 'images': 0, 'tables': 0},
                media_references={'images': [], 'tables': [], 'has_media': False}
            )

        retrieved_chunk_texts_preview = [chunk.get('text', '')[:100] + "..." for chunk in retrieved_chunks]

        logger.info("Step 3: Getting LLM response using multimodal RAG...")
        
        all_retrieved_content = {
            'text_chunks': retrieved_chunks,
            'images': retrieved_images,
            'tables': retrieved_tables
        }
        
        llm_options = {
            "num_predict": settings.LLM_NUM_PREDICT_MULTIMODAL,
            "temperature": settings.LLM_TEMPERATURE,
            "top_p": 0.9,
            "repeat_penalty": 1.1
        }
        
        llm_response_text = process_multimodal_llm_chat_request(
            user_query=query,
            multimodal_content=all_retrieved_content,
            model_name=model_name,
            lang=lang,
            options=llm_options,
            conversation_history=conversation_history
        )
        
        enhanced_response = enhance_response_with_media_references(
            llm_response_text,
            retrieved_images,
            retrieved_tables
        )
    
    except EmbeddingError as e:
        logger.error(f"Embedding error in chat: {e}")
        fallback_message = FallbackResponseService.generate_error_response("embedding_error", e.message, query)
        raise HTTPException(status_code=500, detail=fallback_message)
    except VectorDBError as e:
        logger.error(f"Vector DB error in chat: {e}")
        fallback_message = FallbackResponseService.generate_error_response("vector_error", e.message, query)
        raise HTTPException(status_code=500, detail=fallback_message)
    except LLMError as e:
        logger.error(f"LLM error in chat: {e}")
        fallback_message = FallbackResponseService.generate_error_response("llm_error", e.message, query)
        raise HTTPException(status_code=500, detail=fallback_message)
    except Exception as e:
        logger.error(f"Unexpected error in chat: {e}", exc_info=True)
        fallback_message = FallbackResponseService.generate_error_response("system_error", str(e), query)
        raise HTTPException(status_code=500, detail=fallback_message)

    final_response_text = enhanced_response.get('text', llm_response_text)
    
    if not final_response_text or len(final_response_text.strip()) < 30:
        total_results = len(retrieved_chunks) + len(retrieved_images) + len(retrieved_tables)
        if total_results == 0:
            fallback_data = FallbackResponseService.generate_no_results_response(query, document_ids)
            final_response_text = fallback_data['response']
        else:
            final_response_text = FallbackResponseService.enhance_poor_results_response(
                final_response_text, query, total_results
            )
    
    return ChatResponse(
        query=query,
        response=final_response_text,
        source_document_id=document_ids[0] if document_ids else None,
        retrieved_chunks_preview=retrieved_chunk_texts_preview,
        content_summary={
            'text_chunks': len(retrieved_chunks),
            'images': len(retrieved_images),
            'tables': len(retrieved_tables)
        },
        media_references={
            'images': enhanced_response.get('referenced_images', []),
            'tables': enhanced_response.get('referenced_tables', []),
            'has_media': enhanced_response.get('has_media', False)
        }
    )
