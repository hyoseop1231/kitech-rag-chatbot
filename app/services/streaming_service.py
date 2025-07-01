"""
Streaming service for real-time LLM response delivery
"""
from typing import Dict, Any, Generator, List
from app.services.llm_service import get_llm_response, construct_multimodal_rag_prompt
from app.config import settings
from app.utils.logging_config import get_logger

logger = get_logger(__name__)

def generate_consistent_references(multimodal_content: Dict[str, Any]) -> str:
    """
    Generate consistent reference format from multimodal content
    
    Args:
        multimodal_content: Retrieved content with text, images, tables
        
    Returns:
        str: Formatted reference section
    """
    references = []
    
    # Extract text sources
    text_data = multimodal_content.get("text", multimodal_content.get("text_chunks", []))
    text_sources = set()
    
    if isinstance(text_data, list) and text_data:
        for chunk in text_data:
            if isinstance(chunk, dict):
                source = chunk.get("metadata", {}).get("source_document_id", "")
                if source:
                    text_sources.add(source)
            
    for source in sorted(text_sources):
        references.append(f"📄 {source}")
    
    # Extract table sources  
    tables = multimodal_content.get("tables", [])
    for i, table in enumerate(tables, 1):
        if isinstance(table, dict):
            metadata = table.get("metadata", {})
            source = metadata.get("source_document_id", "")
            page = metadata.get("page", "")
            if source:
                page_info = f" (페이지 {page})" if page else ""
                references.append(f"📊 표{i} - {source}{page_info}")
    
    # Extract image sources
    images = multimodal_content.get("images", [])
    for i, image in enumerate(images, 1):
        if isinstance(image, dict):
            metadata = image.get("metadata", {})
            source = metadata.get("source_document_id", "")
            page = metadata.get("page", "")
            if source:
                page_info = f" (페이지 {page})" if page else ""
                references.append(f"🖼️ 이미지{i} - {source}{page_info}")
    
    if references:
        return f"\n\n## 📚 참고문헌\n" + "\n".join(f"- {ref}" for ref in references)
    
    return ""

def process_multimodal_llm_chat_request_stream(
    user_query: str,
    multimodal_content: Dict[str, Any],
    model_name: str = None,
    lang: str = "ko",
    options: Dict = None,
    conversation_history: List[Dict[str, str]] = None
) -> Generator[str, None, None]:
    """
    Process multimodal chat request with streaming response.
    
    Args:
        user_query: User's question
        multimodal_content: Retrieved content (text, images, tables)
        model_name: LLM model to use
        lang: Language for response
        options: LLM options
        
    Yields:
        str: Streaming response chunks
    """
    try:
        # Extract content from multimodal data - handle both key formats for compatibility
        text_data = multimodal_content.get("text", multimodal_content.get("text_chunks", []))
        if isinstance(text_data, list) and text_data and isinstance(text_data[0], dict):
            # Format: [{"text": "...", "metadata": {...}}, ...]
            context_chunks = [chunk.get("text", "") for chunk in text_data]
        elif isinstance(text_data, list):
            # Format: ["text1", "text2", ...]
            context_chunks = text_data
        else:
            context_chunks = []
        
        images = multimodal_content.get("images", [])
        image_descriptions = [img.get("description", "") for img in images]
        
        tables = multimodal_content.get("tables", [])
        table_contents = [table.get("content", "") for table in tables]
        
        # Construct the multimodal prompt
        prompt = construct_multimodal_rag_prompt(
            user_query,
            context_chunks,
            image_descriptions,
            table_contents,
            lang,
            conversation_history
        )
        
        logger.info(f"Starting streaming response for query: {user_query[:50]}...")
        
        # Get streaming response from LLM
        stream_generator = get_llm_response(
            prompt=prompt,
            model_name=model_name,
            options=options,
            stream=True  # Enable streaming
        )
        
        # Track the full response as we stream it
        full_response_chunks = []
        response_complete = False
        
        for chunk in stream_generator:
            if chunk:  # Only yield non-empty chunks
                yield chunk
                full_response_chunks.append(chunk)
                response_complete = True
        
        # Add consistent references at the end if response was generated AND meaningful
        if response_complete:
            # Reconstruct full response for analysis
            full_response_text = "".join(full_response_chunks)
            
            # Use the same meaningful response logic as multimodal_llm_service.py
            meaningful_response = (
                len(full_response_text) > 500 and  # Response is substantial
                not any(phrase in full_response_text.lower() for phrase in [
                    "정보가 부족", "확인할 수 없습니다", "정보를 찾을 수 없", "내용을 파악하기 어렵",
                    "구체적인 답변을 드릴 수 없", "정보로는", "언급되어 있지 않",
                    "현재 제공된 정보만으로는", "추가적인 정보가 필요", "파악하기 어렵습니다",
                    "포함되어 있지 않습니다", "답변할 수 없습니다", "답변을 드릴 수 없",
                    "관련이 없습니다", "전혀 포함되어 있지 않", "어떠한지 답변할 수 없",
                    "날씨 정보와는 관련이 없", "문서 정보에는", "포함되어 있지 않아", 
                    "알 수 없습니다", "문서의 범위 밖", "범위 밖의 내용", "포함되어 있지 않",
                    "날씨에 대한 답변", "날씨에 대한 내용이 포함", "날씨 정보는"
                ])
            )
            
            # Only add references if we have meaningful content
            if meaningful_response:
                references = generate_consistent_references(multimodal_content)
                if references:
                    yield references
                
        logger.info("Streaming response completed")
        
    except Exception as e:
        logger.error(f"Error in streaming response: {e}")
        yield f"\n\n[오류: {str(e)}]"