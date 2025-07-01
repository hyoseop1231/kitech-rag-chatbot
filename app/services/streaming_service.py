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
    Generate accordion-style reference format from multimodal content with relevance-based sorting
    
    Args:
        multimodal_content: Retrieved content with text, images, tables
        
    Returns:
        str: Formatted reference section with HTML accordion (text documents only, sorted by relevance)
    """
    # Extract text sources with relevance information
    text_refs = []
    text_data = multimodal_content.get("text", multimodal_content.get("text_chunks", []))
    
    if isinstance(text_data, list) and text_data:
        source_info = {}  # Dictionary to track source info with best distance
        
        for chunk in text_data:
            if isinstance(chunk, dict):
                metadata = chunk.get("metadata", {})
                source = metadata.get("source_document_id", "")
                distance = chunk.get("distance", float('inf'))  # Lower distance = higher relevance
                page = metadata.get("page", "")
                
                if source:
                    # Keep the best (lowest) distance for each source
                    if source not in source_info or distance < source_info[source]["distance"]:
                        source_info[source] = {
                            "distance": distance,
                            "page": page,
                            "source": source
                        }
        
        # Sort by relevance (lower distance = higher relevance)
        sorted_sources = sorted(source_info.values(), key=lambda x: x["distance"])
        
        # Create text references with citation numbers
        for i, info in enumerate(sorted_sources, 1):
            source = info["source"]
            page = info["page"]
            page_info = f" (페이지 {page})" if page else ""
            text_refs.append(f"[{i}] 📄 {source}{page_info}")
    
    total_text = len(text_refs)
    
    if total_text == 0:
        return ""
    
    # Determine how many to show initially (max 5)
    max_initial_show = 5
    initial_refs = text_refs[:max_initial_show]
    remaining_refs = text_refs[max_initial_show:]
    
    # Build main reference list
    main_content = chr(10).join(f'        <div style="margin-bottom: 4px;">• {ref}</div>' for ref in initial_refs)
    
    # Build "more" section if there are additional references
    more_section = ""
    if remaining_refs:
        more_content = chr(10).join(f'        <div style="margin-bottom: 4px;">• {ref}</div>' for ref in remaining_refs)
        more_section = f"""
        <details style="margin-top: 8px;">
            <summary style="cursor: pointer; font-size: 0.9em; color: #667eea; padding: 4px 0; user-select: none;">
                📖 더 보기 ({len(remaining_refs)}개 추가 문헌)
            </summary>
            <div style="margin-top: 8px; padding-left: 12px; border-left: 2px solid #e9ecef;">
{more_content}
            </div>
        </details>"""
    
    # Create HTML accordion structure (text documents only)
    accordion_html = f"""

<details style="margin-top: 12px;">
    <summary style="cursor: pointer; display: flex; align-items: center; gap: 8px; font-weight: 600; color: #495057; margin-bottom: 0; padding: 8px 12px; background: rgba(102, 126, 234, 0.08); border-radius: 6px; border: 1px solid rgba(102, 126, 234, 0.2); user-select: none;">
        📚 참조 문헌 및 출처 정보
        <span style="font-size: 0.8em; color: #6c757d; margin-left: auto;">
            (📄 {total_text}개 문헌 | 클릭하여 확장)
        </span>
    </summary>
    <div style="margin-top: 12px; padding: 12px; background: #f8f9fa; border-radius: 6px; border: 1px solid #e9ecef;">
{main_content}{more_section}
        <div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid #dee2e6; font-size: 0.85em; color: #6c757d;">
            💡 이 답변은 위의 문서 콘텐츠를 기반으로 생성되었습니다.
        </div>
    </div>
</details>"""
    
    return accordion_html

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
        prompt, is_reasoning_model = construct_multimodal_rag_prompt(
            user_query,
            context_chunks,
            image_descriptions,
            table_contents,
            lang,
            conversation_history,
            model_name
        )
        
        # Add think-step-by-step for non-reasoning models
        if not is_reasoning_model:
            prompt = f"Think step-by-step.\n{prompt}"
        
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
            # Lower the minimum length threshold and add more sophisticated checks
            meaningful_response = (
                len(full_response_text) > 100 and  # Reduced from 500 to 100 characters
                len(full_response_text.strip()) > 50 and  # At least 50 non-whitespace chars
                not any(phrase in full_response_text.lower() for phrase in [
                    "정보가 부족", "확인할 수 없습니다", "정보를 찾을 수 없", "내용을 파악하기 어렵",
                    "구체적인 답변을 드릴 수 없", "정보로는", "언급되어 있지 않",
                    "현재 제공된 정보만으로는", "추가적인 정보가 필요", "파악하기 어렵습니다",
                    "포함되어 있지 않습니다", "답변할 수 없습니다", "답변을 드릴 수 없",
                    "관련이 없습니다", "전혀 포함되어 있지 않", "어떠한지 답변할 수 없",
                    "날씨 정보와는 관련이 없", "문서 정보에는", "포함되어 있지 않아", 
                    "알 수 없습니다", "문서의 범위 밖", "범위 밖의 내용", "포함되어 있지 않",
                    "날씨에 대한 답변", "날씨에 대한 내용이 포함", "날씨 정보는"
                ]) and
                # Additional check: ensure there are actual document references available
                bool(multimodal_content.get("text", []) or multimodal_content.get("text_chunks", []))
            )
            
            # Only add references if we have meaningful content
            if meaningful_response:
                references = generate_consistent_references(multimodal_content)
                if references:
                    # 참고문헌을 한 번에 전송하여 chunk 분할 방지
                    yield f"\n\n{references}"
                
        logger.info("Streaming response completed")
        
    except Exception as e:
        logger.error(f"Error in streaming response: {e}")
        yield f"\n\n[오류: {str(e)}]"