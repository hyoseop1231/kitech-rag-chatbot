from fastapi import APIRouter, File, UploadFile, Form, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse
import shutil
import os
import time
import uuid # For unique document IDs
import requests
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 서비스 모듈 임포트
from app.services.ocr_service import extract_multimodal_content_from_pdf, extract_text_from_pdf
from app.services.text_processing_service import split_text_into_chunks, get_embeddings
from app.services.vector_db_service import get_all_documents, delete_document, delete_all_documents, get_document_info
from app.services.vector_db_service import store_multimodal_content, search_multimodal_content, delete_multimodal_document, get_multimodal_document_info, delete_all_multimodal_documents
from app.services.multimodal_llm_service import process_multimodal_llm_chat_request, enhance_response_with_media_references
from app.services.streaming_service import process_multimodal_llm_chat_request_stream
from app.config import settings
from app.utils.logging_config import get_logger
from app.utils.security import FileValidator, sanitize_input, validate_document_id
from app.utils.monitoring import get_monitor
from app.utils.file_manager import DocumentFileManager
from app.utils.query_validator import QueryValidator
from app.services.fallback_response_service import FallbackResponseService
from app.utils.exceptions import ValidationError, FileProcessingError, OCRError, VectorDBError, EmbeddingError, LLMError

logger = get_logger(__name__)

# Pydantic 모델 (요청/응답 본문 정의)
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class ChatRequest(BaseModel):
    query: str
    document_id: Optional[str] = None # (하위 호환)
    document_ids: Optional[List[str]] = None # 여러 문서 ID에 대해 질문할 경우
    model_name: Optional[str] = settings.OLLAMA_DEFAULT_MODEL
    lang: Optional[str] = "ko"
    conversation_history: Optional[List[Dict[str, str]]] = None # 대화 히스토리

class ChatResponse(BaseModel):
    query: str
    response: str
    source_document_id: Optional[str] = None
    retrieved_chunks_preview: Optional[List[str]] = None # 디버깅/정보용
    content_summary: Optional[Dict[str, int]] = None # 멀티모달 컨텐츠 요약
    media_references: Optional[Dict[str, Any]] = None # 이미지/표 참조 정보

router = APIRouter()

# 업로드 디렉토리는 settings에서 관리

# PDF 처리 상태 저장 (간단한 인메모리 방식)
pdf_processing_status = {}

executor = ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_FILE_PROCESSING)  # 최대 동시 파일 처리 개수

from app.services.text_processing_service import process_multimodal_pdf_and_store

def process_pdf_background_entry(
    file_path,
    document_id,
    filename: str,
    ocr_correction_enabled: bool,
    llm_correction_enabled: bool
):
    # 백그라운드 처리용 엔트리 함수 (threaded)
    from app.api.endpoints import process_pdf_background
    process_pdf_background(
        file_path,
        document_id,
        filename,
        ocr_correction_enabled,
        llm_correction_enabled
    )

def process_pdf_background(
    file_path: str,
    document_id: str,
    filename: str,
    ocr_correction_enabled: bool,
    llm_correction_enabled: bool
):
    """
    백그라운드에서 PDF의 모든 콘텐츠(텍스트, 이미지, 표)를 추출하고 저장합니다.
    """
    logger.info(f"🚀 Background task started: Processing PDF {document_id} from {file_path}")
    logger.info(f"Correction settings: OCR={ocr_correction_enabled}, LLM={llm_correction_enabled}")
    
    # 기존 상태 정보 유지하면서 처리 시작 상태로 업데이트
    current_status = pdf_processing_status.get(document_id, {})
    was_queued = current_status.get("step") == "Queued"
    
    # 백그라운드 작업 시작 확인 상태로 업데이트
    pdf_processing_status[document_id] = {
        "step": "Starting",
        "message": "큐에서 처리 시작됨" if was_queued else "백그라운드 처리 시작됨",
        "percent": 1,
        "current_page": 0,
        "total_pages": 0,
        "details": {"started": True, "was_queued": was_queued},
        "timestamp": __import__('datetime').datetime.now().isoformat()
    }
    logger.info(f"✅ Background processing status updated for: {document_id}")
    
    def update_status(step: str, message: str, percent: int, current_page: int = 0, total_pages: int = 0, 
                     details: dict = None):
        """상세한 진행 상태 업데이트"""
        status = {
            "step": step,
            "message": message,
            "percent": percent,
            "current_page": current_page,
            "total_pages": total_pages,
            "details": details or {},
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }
        pdf_processing_status[document_id] = status
        logger.info(f"[Task {document_id}] {step}: {message} ({percent}%)")
    
    try:
        # 0. PDF 분석 시작
        update_status("Analyzing", "PDF 파일 분석 중...", 5)
        
        # PDF 페이지 수 확인
        import fitz  # PyMuPDF
        try:
            doc = fitz.open(file_path)
            total_pages = len(doc)
            doc.close()
            logger.info(f"[Task {document_id}] PDF has {total_pages} pages")
        except Exception as e:
            logger.warning(f"[Task {document_id}] Could not determine page count: {e}")
            total_pages = 0
        
        # 1. 멀티모달 콘텐츠 추출
        update_status("OCR", f"OCR 및 콘텐츠 추출 시작... (총 {total_pages}페이지)", 10, 0, total_pages)
        
        logger.info(f"[Task {document_id}] Step 1: Extracting multimodal content...")
        try:
            # OCR 진행률 콜백 함수 (상세 단계 포함)
            def ocr_progress_callback(current_page: int, total_pages: int, stage: str, custom_message: str = None):
                page_progress = (current_page / total_pages) if total_pages > 0 else 0
                overall_progress = 10 + (page_progress * 40)  # 빠른 추출은 10-50% 차지
                
                stage_messages = {
                    "text": f"텍스트 추출 중... ({current_page}/{total_pages} 페이지)",
                    "images": f"이미지 추출 중... ({current_page}/{total_pages} 페이지)",  
                    "tables": f"표 추출 중... ({current_page}/{total_pages} 페이지)",
                    "table_preprocessing": f"표 전처리 중... ({current_page}/{total_pages} 페이지)",
                    "table_detection": f"표 구조 분석 중... ({current_page}/{total_pages} 페이지)",
                    "table_processing": custom_message or f"표 처리 중... ({current_page}/{total_pages} 페이지)",
                    "table_ocr": custom_message or f"표 OCR 중... ({current_page}/{total_pages} 페이지)"
                }
                
                message = custom_message or stage_messages.get(stage, f"페이지 처리 중... ({current_page}/{total_pages})")
                details = {
                    "stage": stage,
                    "pages_processed": current_page,
                    "images_found": 0,
                    "tables_found": 0,
                    "substage": stage if stage.startswith("table_") else None
                }
                
                update_status("OCR", message, int(overall_progress), current_page, total_pages, details)
            
            content_data = extract_multimodal_content_from_pdf(
                file_path,
                document_id,
                ocr_correction_enabled,
                llm_correction_enabled,
                progress_callback=ocr_progress_callback
            )
            extracted_text = content_data.get('text', '')
            extracted_images = content_data.get('images', [])
            extracted_tables = content_data.get('tables', [])
            
            if not extracted_text and not extracted_images and not extracted_tables:
                raise OCRError("No content extracted from PDF", "EMPTY_EXTRACTION")
            
            # 빠른 추출 완료 상태
            extract_details = {
                "text_length": len(extracted_text),
                "images_count": len(extracted_images),
                "tables_count": len(extracted_tables),
                "pages_processed": total_pages
            }
            update_status("FastExtract", f"빠른 추출 완료! 텍스트: {len(extracted_text)}자, 이미지: {len(extracted_images)}개, 표: {len(extracted_tables)}개", 
                         50, total_pages, total_pages, extract_details)
            
            logger.info(f"[Task {document_id}] Extracted: text={len(extracted_text)} chars, images={len(extracted_images)}, tables={len(extracted_tables)}")

        except (OCRError, FileProcessingError) as e:
            update_status("Error", f"OCR 오류: {e.message}", 0, 0, total_pages, {"error": str(e)})
            logger.error(f"[Task {document_id}] OCR error: {e}")
            # 실패 시 파일 정리
            _cleanup_failed_processing(file_path, document_id)
            return

        # 2-4. 세분화된 텍스트 처리 (청킹, 임베딩, 저장)
        logger.info(f"[Task {document_id}] Step 2-4: Processing text content...")
        
        try:
            # 세밀한 텍스트 처리 직접 실행 (세분화된 진행률 포함)
            from app.services.text_processing_service import split_text_into_chunks_with_progress, get_embeddings
            from app.services.vector_db_service import store_multimodal_content
            
            # 텍스트가 있는 경우에만 세밀한 청킹 처리
            if extracted_text and extracted_text.strip():
                # 세밀한 텍스트 분할 단계별 콜백
                def detailed_chunking_callback(total_pages, current_page, step, message):
                    step_percentages = {
                        "text_preprocessing": 56,
                        "text_splitting": 58, 
                        "chunk_validation": 60,
                        "chunk_correction": 61,
                        "chunk_preparation": 62
                    }
                    percent = step_percentages.get(step, 60)
                    details = {
                        "stage": step,
                        "text_length": len(extracted_text),
                        "processed": True
                    }
                    update_status(step, message, percent, total_pages, total_pages, details)
                
                # 세밀한 청크 분할 실행
                text_chunks = split_text_into_chunks_with_progress(
                    extracted_text,
                    progress_callback=detailed_chunking_callback,
                    total_pages=total_pages
                )
                
                # 임베딩 생성 (65%)
                update_status("Embedding", f"{len(text_chunks)}개 청크 임베딩 생성 중...", 65, total_pages, total_pages, 
                            {"chunks_count": len(text_chunks)})
                text_embeddings = get_embeddings(text_chunks)
                
                # 메타데이터 준비 (75%)
                update_status("Metadata", "메타데이터 준비 중...", 75, total_pages, total_pages, {})
                text_metadatas = [
                    {"source_document_id": document_id, "filename": filename, "chunk_index": i, "content_type": "text"}
                    for i in range(len(text_chunks))
                ]
                
                # 데이터베이스 저장 (80%)
                update_status("Storing", "벡터 데이터베이스에 저장 중...", 80, total_pages, total_pages, {})
                store_multimodal_content(
                    document_id=document_id,
                    content_data={
                        "text_chunks": text_chunks,
                        "images": extracted_images,
                        "tables": extracted_tables
                    },
                    text_vectors=text_embeddings,
                    text_metadatas=text_metadatas
                )
                
                result = {
                    "text_chunks_stored": len(text_chunks),
                    "images_stored": len(extracted_images),
                    "tables_stored": len(extracted_tables)
                }
            else:
                # 텍스트가 없으면 이미지/표만 저장
                update_status("Storing", "이미지/표 데이터 저장 중...", 80, total_pages, total_pages, {})
                store_multimodal_content(
                    document_id=document_id,
                    content_data={
                        "text_chunks": [],
                        "images": extracted_images,
                        "tables": extracted_tables
                    },
                    text_vectors=[],
                    text_metadatas=[]
                )
                
                result = {
                    "text_chunks_stored": 0,
                    "images_stored": len(extracted_images),
                    "tables_stored": len(extracted_tables)
                }
            
            text_chunks = result.get("text_chunks_stored", 0)
            extracted_images = result.get("images_stored", 0)
            extracted_tables = result.get("tables_stored", 0)
            
            logger.info(f"[Task {document_id}] Successfully processed and stored multimodal content")
            
        except Exception as e:
            update_status("Error", f"텍스트 처리 오류: {str(e)}", 75, total_pages, total_pages, {"error": str(e)})
            logger.error(f"[Task {document_id}] Text processing error: {e}")
            # 실패 시 파일 정리
            _cleanup_failed_processing(file_path, document_id)
            return

        # 5. 최종 상태 업데이트 - 완료 처리
        final_message = f"처리 완료! 텍스트: {text_chunks}청크, 이미지: {extracted_images}개, 표: {extracted_tables}개"
        final_details = {
            "total_pages": total_pages,
            "text_chunks": text_chunks,
            "images": extracted_images,
            "tables": extracted_tables,
            "text_length": len(extracted_text) if extracted_text else 0,
            "processing_time": None
        }
        update_status("Completed", final_message, 100, total_pages, total_pages, final_details)
        logger.info(f"[Task {document_id}] Successfully processed and stored multimodal content for: {document_id}")
        
        # 완료 후 일정 시간 후 상태 정리 (15초)
        import threading
        def cleanup_status():
            import time
            time.sleep(15)
            if document_id in pdf_processing_status:
                del pdf_processing_status[document_id]
                logger.info(f"[Task {document_id}] Status cleaned up from memory")
        
        cleanup_thread = threading.Thread(target=cleanup_status)
        cleanup_thread.daemon = True
        cleanup_thread.start()

    except Exception as e:
        pdf_processing_status[document_id] = {"step": "Error", "message": f"예외 발생: {str(e)}", "percent": 0}
        logger.error(f"[Task {document_id}] Unexpected error during background PDF processing: {e}", exc_info=True)
        # 예외 발생 시 파일 정리
        _cleanup_failed_processing(file_path, document_id)
    finally:
        # 메모리 정리
        import gc
        gc.collect()

def _cleanup_failed_processing(file_path: str, document_id: str):
    """
    처리 실패 시 파일 및 관련 데이터 정리
    """
    try:
        # 1. 업로드된 파일 삭제
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up failed upload file: {file_path}")
        
        # 2. 부분적으로 생성된 컨텐츠 디렉토리 정리
        content_dir = os.path.join(settings.UPLOAD_DIR, f"{document_id}_content")
        if os.path.exists(content_dir):
            import shutil
            shutil.rmtree(content_dir)
            logger.info(f"Cleaned up content directory: {content_dir}")
        
        # 3. Vector DB에서 부분적으로 저장된 데이터 삭제
        try:
            delete_multimodal_document(document_id)
            logger.info(f"Cleaned up vector DB data for: {document_id}")
        except Exception as db_error:
            logger.warning(f"Failed to clean up vector DB for {document_id}: {db_error}")
        
        # 4. 처리 상태에서 제거
        if document_id in pdf_processing_status:
            del pdf_processing_status[document_id]
        
        logger.info(f"Cleanup completed for failed processing: {document_id}")
        
    except Exception as cleanup_error:
        logger.error(f"Error during cleanup for {document_id}: {cleanup_error}")


@router.post("/upload_pdf/")
async def upload_pdf(
    files: List[UploadFile] = File(...),
    ocr_correction_enabled: bool = Form(False),
    llm_correction_enabled: bool = Form(False)
):
    f"""
    여러 PDF 파일 업로드를 지원합니다. 각 파일은 병렬로 OCR/임베딩/저장 처리됩니다.
    최대 {settings.MAX_CONCURRENT_FILE_PROCESSING}개 파일까지만 동시 처리 가능합니다.
    """
    # 파일 개수 제한
    if len(files) > settings.MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=400, 
            detail=f"최대 {settings.MAX_FILES_PER_UPLOAD}개의 파일까지만 한 번에 업로드할 수 있습니다. (현재: {len(files)}개)"
        )
    
    logger.info(f"Processing {len(files)} files (max {settings.MAX_CONCURRENT_FILE_PROCESSING} concurrent)")
    results = []
    for file in files:
        logger.info(f"Upload request received for file: {file.filename}")
        # Basic validation
        if not file.filename:
            results.append({"filename": None, "error": "No filename provided"})
            continue
        # Check file size
        content = await file.read()
        file_size = len(content)
        if file_size == 0:
            results.append({"filename": file.filename, "error": "Empty file uploaded"})
            continue
        # Generate secure document ID and filename
        document_id = f"{os.path.splitext(file.filename)[0]}_{str(uuid.uuid4())[:8]}"
        safe_filename = FileValidator.generate_safe_filename(file.filename, document_id)
        # Save path using pathlib
        file_path = Path(settings.UPLOAD_DIR) / safe_filename
        try:
            # Save file
            with open(file_path, "wb") as buffer:
                buffer.write(content)
            logger.info(f"File saved: {file_path} ({file_size} bytes)")
            # Validate uploaded file
            validation_result = FileValidator.validate_uploaded_file(file_path, file.filename, file_size)
            if not validation_result["is_valid"]:
                os.remove(file_path)
                logger.warning(f"File validation failed: {validation_result['errors']}")
                results.append({"filename": file.filename, "error": f"File validation failed: {'; '.join(validation_result['errors'])}"})
                continue
            # 현재 활성 작업 수 확인
            active_tasks = len([status for status in pdf_processing_status.values() 
                              if status.get("step") not in ["Done", "Completed", "Error", "Queued"]])
            
            # 큐에서 대기 중인지 확인
            is_queued = active_tasks >= settings.MAX_CONCURRENT_FILE_PROCESSING
            
            # 처리 상태 초기화 (백그라운드 시작 전에 먼저 설정)
            initial_step = "Queued" if is_queued else "Preparing"
            initial_message = "처리 대기열에서 순서를 기다리는 중..." if is_queued else "문서 처리 준비 중..."
            
            pdf_processing_status[document_id] = {
                "step": initial_step,
                "message": initial_message,
                "percent": 0,
                "current_page": 0,
                "total_pages": 0,
                "details": {"queued": is_queued, "queue_position": active_tasks + 1 if is_queued else 0},
                "timestamp": __import__('datetime').datetime.now().isoformat()
            }
            logger.info(f"✅ Processing status initialized for document: {document_id} (queued: {is_queued})")
            
            # Start background processing (스레드 기반)
            future = executor.submit(
                process_pdf_background_entry,
                file_path,
                document_id,
                file.filename,
                ocr_correction_enabled,
                llm_correction_enabled
            )
            logger.info(f"Background processing started for document: {document_id}")
            results.append({
                "message": "File uploaded successfully. Processing started in the background.",
                "filename": file.filename,
                "document_id": document_id,
                "file_hash": validation_result["file_hash"],
                "detail": "The PDF is being processed. This may take some time depending on the file size and content."
            })
        except Exception as e:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"Cleaned up file after error: {file_path}")
                except OSError as cleanup_error:
                    logger.error(f"Error cleaning up file {file_path}: {cleanup_error}")
            logger.error(f"Unexpected error during file upload: {e}")
            results.append({"filename": file.filename, "error": "Could not save or start processing file"})
    return JSONResponse(content={"results": results}, status_code=202)


@router.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    monitor = get_monitor()
    health_status = monitor.check_health()
    
    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(content=health_status, status_code=status_code)

@router.get("/metrics")
async def get_metrics():
    """Get application performance metrics"""
    monitor = get_monitor()
    stats = monitor.get_stats()
    
    return JSONResponse(content={
        "performance": stats,
        "timestamp": time.time()
    })

@router.post("/chat/stream/")
async def chat_with_documents_stream(request: ChatRequest):
    """
    Process a chat query with streaming response for real-time user experience.
    """
    query = request.query
    model_name = request.model_name
    lang = request.lang or "ko"
    conversation_history = request.conversation_history or []
    logger.info(f"Stream request - conversation history: {len(conversation_history)} messages")
    
    # Support both single document_id and multiple document_ids
    document_ids = []
    if request.document_ids:
        document_ids = request.document_ids
    elif request.document_id:
        document_ids = [request.document_id]
    

    # Process real-time streaming
    async def stream_response():
        import json
        
        try:
            # 1. Query embedding
            yield f"data: {json.dumps({'type': 'status', 'message': '질문 분석 중...'}, ensure_ascii=False)}\n\n"
            
            import time
            start_time = time.time()
            
            # 질문 유효성 검증
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
                # 검색을 위한 질문 향상
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
                # Apply document filter if specified
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
            
            # Debug: Log search results
            retrieved_chunks = multimodal_results.get('text', [])
            retrieved_images = multimodal_results.get('images', [])
            retrieved_tables = multimodal_results.get('tables', [])
            
            logger.info(f"Streaming search results: text={len(retrieved_chunks)}, images={len(retrieved_images)}, tables={len(retrieved_tables)}")
            
            # Check if we have any search results
            if not retrieved_chunks and not retrieved_images and not retrieved_tables:
                fallback_data = FallbackResponseService.generate_no_results_response(query, document_ids)
                yield f"data: {json.dumps({'type': 'no_results', 'message': fallback_data['response'], 'suggestions': fallback_data['suggestions']}, ensure_ascii=False)}\n\n"
                return
            
            # Debug: Log actual content
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
            
            # Get streaming response from LLM
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
                
                # Send complete words/phrases instead of single characters (Korean-optimized)
                korean_delimiters = [' ', '\n', '.', ',', '!', '?', ')', ']', '}', '다.', '다,', '다!', '다?', '요.', '요,', '요!', '요?']
                if any(delimiter in word_buffer for delimiter in korean_delimiters) or len(word_buffer) > 50:
                    chunk_data = {
                        "type": "content",
                        "content": word_buffer,
                        "is_final": False
                    }
                    yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                    word_buffer = ""
            
            # Send any remaining content
            if word_buffer:
                chunk_data = {
                    "type": "content",
                    "content": word_buffer,
                    "is_final": False
                }
                yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
            
            # Enhance and cache the complete response
            enhanced_response = enhance_response_with_media_references(
                full_response,
                retrieved_images,
                retrieved_tables
            )
            
            # Validate and enhance response
            final_response_text = enhanced_response.get('text', full_response) if enhanced_response else full_response
            if not final_response_text or not final_response_text.strip():
                fallback_data = FallbackResponseService.generate_no_results_response(query, document_ids)
                final_response_text = fallback_data['response']
            elif len(final_response_text.strip()) < 50:
                # 응답이 너무 짧은 경우 향상
                total_results = len(retrieved_chunks) + len(retrieved_images) + len(retrieved_tables)
                final_response_text = FallbackResponseService.enhance_poor_results_response(
                    final_response_text, query, total_results
                )
            
            response_data = {
                'query': query,
                'response': final_response_text,
                'source_document_id': document_ids[0] if document_ids else None,
                'retrieved_chunks_preview': [chunk.get('text', '')[:100] + "..." for chunk in retrieved_chunks if chunk.get('text')],
                'content_summary': {
                    'text_chunks': len(retrieved_chunks),
                    'images': len(retrieved_images),
                    'tables': len(retrieved_tables)
                },
                'media_references': {
                    'images': enhanced_response.get('referenced_images', []) if enhanced_response else [],
                    'tables': enhanced_response.get('referenced_tables', []) if enhanced_response else [],
                    'has_media': enhanced_response.get('has_media', False) if enhanced_response else False
                }
            }
            
            
            # Send final metadata
            final_data = {
                "type": "final",
                "metadata": {
                    "content_summary": response_data['content_summary'],
                    "media_references": response_data['media_references'],
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
    1. Validates and sanitizes user input
    2. Embeds the user query
    3. Searches for relevant chunks in the Vector DB (optionally filtered by document_id)
    4. Constructs a RAG prompt and gets a response from the LLM
    """
    # Input validation and sanitization
    query = sanitize_input(chat_request.query, max_length=2000)
    if not query:
        raise HTTPException(status_code=400, detail="Query not provided or invalid.")
    
    # Extract conversation history
    conversation_history = chat_request.conversation_history or []
    logger.info(f"Received conversation history: {len(conversation_history)} messages")
    
    # 질문 유효성 검증
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
    
    # Validate document IDs
    document_ids = chat_request.document_ids or ([] if chat_request.document_id is None else [chat_request.document_id])
    if document_ids:
        for doc_id in document_ids:
            if not validate_document_id(doc_id):
                raise HTTPException(status_code=400, detail=f"Invalid document ID: {doc_id}")
    
    model_name = chat_request.model_name or settings.OLLAMA_DEFAULT_MODEL
    lang = chat_request.lang or "ko"

    logger.info(f"Chat request: Query length={len(query)}, DocIDs={len(document_ids)}, Model={model_name}, Lang={lang}")
    logger.debug(f"Query preview: {query[:100]}...")

    try:
        # 1. Embed user query (with enhancement)
        logger.info("Step 1: Embedding user query...")
        enhanced_query = QueryValidator.enhance_query_for_search(query)
        query_embedding_list = get_embeddings([enhanced_query])
        if not query_embedding_list or not query_embedding_list[0]:
            raise EmbeddingError("Could not generate embedding for the query", "QUERY_EMBEDDING_FAILED")
        query_embedding = query_embedding_list[0]

        # 2. Search Multimodal Content
        logger.info("Step 2: Searching multimodal content for relevant information...")
        filter_metadata = None
        if document_ids and len(document_ids) > 0:
            if len(document_ids) == 1:
                filter_metadata = {"source_document_id": document_ids[0]}
            else:
                filter_metadata = {"source_document_id": {"$in": document_ids}}
            logger.debug(f"Applying filter: {filter_metadata}")
        
        # Search across all content types
        multimodal_results = search_multimodal_content(
            query_vector=query_embedding, 
            top_k=5, 
            filter_metadata=filter_metadata,
            include_images=True,
            include_tables=True
        )
        
        # Extract text results for backward compatibility
        retrieved_chunks = multimodal_results.get('text', [])
        retrieved_images = multimodal_results.get('images', [])
        retrieved_tables = multimodal_results.get('tables', [])

        if not any([retrieved_chunks, retrieved_images, retrieved_tables]):
            logger.warning("No relevant content found in Vector DB")
            # 검색 결과가 없는 경우 대체 응답 생성
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

        # 3. Get LLM response via Multimodal RAG
        logger.info("Step 3: Getting LLM response using multimodal RAG...")
        
        # Combine all retrieved content for LLM processing
        all_retrieved_content = {
            'text_chunks': retrieved_chunks,
            'images': retrieved_images,
            'tables': retrieved_tables
        }
        
        # Use multimodal LLM service with optimized options
        llm_options = {
            "num_predict": settings.LLM_NUM_PREDICT_MULTIMODAL,
            "temperature": settings.LLM_TEMPERATURE,
            "top_p": 0.9,  # 응답 일관성 향상
            "repeat_penalty": 1.1  # 반복 방지
        }
        
        llm_response_text = process_multimodal_llm_chat_request(
            user_query=query,
            multimodal_content=all_retrieved_content,
            model_name=model_name,
            lang=lang,
            options=llm_options,
            conversation_history=conversation_history
        )
        
        # Enhance response with media references for UI display
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

    # Enhanced response with multimodal content info and fallback handling
    final_response_text = enhanced_response.get('text', llm_response_text)
    
    # 응답 품질 검증 및 향상
    if not final_response_text or len(final_response_text.strip()) < 30:
        total_results = len(retrieved_chunks) + len(retrieved_images) + len(retrieved_tables)
        if total_results == 0:
            fallback_data = FallbackResponseService.generate_no_results_response(query, document_ids)
            final_response_text = fallback_data['response']
        else:
            final_response_text = FallbackResponseService.enhance_poor_results_response(
                final_response_text, query, total_results
            )
    
    response_data = {
        'query': query,
        'response': final_response_text,
        'source_document_id': document_ids[0] if document_ids else None,
        'retrieved_chunks_preview': retrieved_chunk_texts_preview,
        'content_summary': {
            'text_chunks': len(retrieved_chunks),
            'images': len(retrieved_images),
            'tables': len(retrieved_tables)
        },
        'media_references': {
            'images': enhanced_response.get('referenced_images', []),
            'tables': enhanced_response.get('referenced_tables', []),
            'has_media': enhanced_response.get('has_media', False)
        }
    }
    
    
    return ChatResponse(**response_data)

@router.get("/upload_status/{document_id}")
def get_upload_status(document_id: str):
    status = pdf_processing_status.get(document_id)
    if status:
        logger.debug(f"📊 Status check for {document_id}: {status['step']} - {status.get('percent', 0)}%")
        return status
    else:
        # 더 상세한 디버깅 정보 제공
        available_ids = list(pdf_processing_status.keys())
        logger.warning(f"⚠️ Status not found for document: {document_id}")
        logger.warning(f"📋 Available document IDs ({len(available_ids)}): {available_ids}")
        
        # 비슷한 ID가 있는지 확인 (오타나 ID 변경 감지)
        similar_ids = [id for id in available_ids if document_id in id or id in document_id]
        if similar_ids:
            logger.info(f"🔍 Similar IDs found: {similar_ids}")
        
        # 오래된 상태 정보 정리 (1시간 이상 된 것)
        import datetime
        current_time = datetime.datetime.now()
        cleaned_count = 0
        
        for doc_id, doc_status in list(pdf_processing_status.items()):
            try:
                timestamp_str = doc_status.get('timestamp')
                if timestamp_str:
                    timestamp = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00').replace('+00:00', ''))
                    age_minutes = (current_time - timestamp).total_seconds() / 60
                    
                    # 1시간 이상 된 완료/에러 상태는 정리
                    if age_minutes > 60 and doc_status.get('step') in ['Done', 'Completed', 'Error']:
                        del pdf_processing_status[doc_id]
                        cleaned_count += 1
                        logger.info(f"🧹 Cleaned old status for {doc_id} (age: {age_minutes:.1f} minutes)")
            except Exception as e:
                logger.debug(f"Failed to parse timestamp for {doc_id}: {e}")
        
        if cleaned_count > 0:
            logger.info(f"🧹 Cleaned {cleaned_count} old status entries")
        
        return {
            "step": "Unknown", 
            "message": f"해당 문서의 상태 정보를 찾을 수 없습니다. (ID: {document_id})",
            "percent": 0,
            "details": {
                "available_count": len(pdf_processing_status),
                "requested_id": document_id,
                "similar_ids": similar_ids,
                "cleaned_old_entries": cleaned_count,
                "debug_info": f"백엔드에서 {len(available_ids)}개 문서 상태를 추적 중"
            }
        }

@router.get("/ollama/status")
def ollama_status():
    """
    Ollama 서버가 실행 중인지 확인합니다.
    """
    # Check Ollama server availability: try /api/models then fallback to /api/tags
    endpoints = []
    base = settings.OLLAMA_API_URL
    endpoints.append(base.replace('/api/generate', '/api/models'))
    endpoints.append(base.replace('/api/generate', '/api/tags'))
    last_detail = None
    for url in endpoints:
        try:
            resp = requests.get(url, timeout=settings.OLLAMA_TIMEOUT)
            if resp.status_code == 200:
                return {"status": "running", "endpoint": url}
            last_detail = resp.text
        except Exception as e:
            last_detail = str(e)
            continue
    # All attempts failed
    return {
        "status": "unreachable",
        "detail": last_detail or "no response",
        "hint": "Ollama 서버가 실행 중인지 확인하세요 (예: 'ollama serve') 또는 OLLAMA_API_URL 설정을 검토하세요."
    }

@router.get("/ollama/models")
def ollama_models(force_refresh: bool = False, quick: bool = True):
    """
    Ollama에 다운로드된 모델 목록을 반환합니다.
    캐싱을 통해 성능을 향상시키고, 필요시에만 LLM으로부터 모델 정보를 가져옵니다.
    
    Args:
        force_refresh: 캐시를 무시하고 강제로 새로고침할지 여부
        quick: 빠른 응답을 위해 기본 모델 목록 반환 (기본값: True)
    """
    if quick:
        # 빠른 응답을 위해 폴백 먼저 시도
        try:
            fallback_result = _fallback_model_list()
            if fallback_result.get("models"):
                logger.info(f"Quick response: returning {len(fallback_result['models'])} models")
                return fallback_result
        except Exception as e:
            logger.warning(f"Quick fallback failed: {e}")
    
    try:
        # 백그라운드에서 캐시 업데이트 시도 (비동기적으로)
        import threading
        
        def background_cache_update():
            try:
                from app.services.model_info_service import get_cached_model_info
                get_cached_model_info(force_refresh=force_refresh)
                logger.info("Background model info cache update completed")
            except Exception as e:
                logger.error(f"Background cache update failed: {e}")
        
        if not quick:
            # 즉시 모드에서는 직접 캐시 확인
            from app.services.model_info_service import get_cached_model_info
            
            logger.info(f"Fetching model info (force_refresh={force_refresh})")
            model_data = get_cached_model_info(force_refresh=force_refresh)
            
            if model_data.get("models"):
                logger.info(f"Returning {len(model_data['models'])} models with cached info")
                return model_data
        else:
            # 퀵 모드에서는 백그라운드 업데이트만 시작
            if not force_refresh:  # 강제 새로고침이 아닐 때만 백그라운드 실행
                thread = threading.Thread(target=background_cache_update, daemon=True)
                thread.start()
        
        # 폴백: 기본 모델 목록 반환
        return _fallback_model_list()
        
    except Exception as e:
        logger.error(f"Error fetching model info: {e}")
        # 최종 폴백: 기본 모델 목록만 반환
        return _fallback_model_list()

def _fallback_model_list():
    """폴백: 기본 모델 목록 반환"""
    # Try listing via /api/models then fallback to /api/tags
    base = settings.OLLAMA_API_URL
    urls = [
        base.replace('/api/generate', '/api/models'),
        base.replace('/api/generate', '/api/tags'),
    ]
    raw_models = []
    detail = None
    for url in urls:
        try:
            resp = requests.get(url, timeout=settings.OLLAMA_TIMEOUT)
            if resp.status_code != 200:
                detail = resp.text
                continue
            data = resp.json()
            # parse possible response structures
            if isinstance(data, dict) and 'models' in data:
                mlist = data.get('models') or []
                if mlist and isinstance(mlist[0], dict) and 'name' in mlist[0]:
                    raw_models = [m.get('name', '') for m in mlist]
                else:
                    raw_models = list(mlist)
            elif isinstance(data, list):
                raw_models = data
            else:
                raw_models = []
            break
        except Exception as e:
            detail = str(e)
            continue
    else:
        # all attempts failed
        return {
            "models": [],
            "summaries": [],
            "detail": detail or 'no response',
            "hint": "Ollama 서버가 실행 중인지 확인하세요 (예: 'ollama serve') 또는 OLLAMA_API_URL 설정을 검토하세요."
        }
    # Summaries for known models
    MODEL_SUMMARIES = {
        "seokdong-llama-3.1-8b": {"display_name": "SEOKDONG-Llama 3.1 (8B)",
            "provider": "Kwangsuklee · Meta 기반", "params": "8 B (Q5_K_M 양자화 ≈ 5.7 GB)",
            "key_points": "한국어 특화 SFT, 128 K 컨텍스트, 2025년 韓 LLM 리더보드 6위"},
        "sfr-embedding-mistral-7b": {"display_name": "SFR-Embedding (Mistral-7B)",
            "provider": "Salesforce Research", "params": "7 B",
            "key_points": "E5-Mistral-7B 기반 임베딩 전용 모델, MTEB 상위권 성능"},
        "llama-3.1-8b": {"display_name": "Llama 3.1 (8B)",
            "provider": "Meta", "params": "8 B",
            "key_points": "다국어 지원, 128 K 컨텍스트, 범용 베이스 모델"},
        "qwen-3-235b-a22b": {"display_name": "Qwen 3 235B-A22B", 
            "provider": "Alibaba Cloud", "params": "235 B 총 (활성 22 B MoE)",
            "key_points": "94-레이어 MoE·GQA 구조, 32 K→131 K 컨텍스트 확장, 고난도 추론·코딩 강점"},
        "llama-3.2-3b-instruct": {"display_name": "Llama 3.2 (3B Instruct)",
            "provider": "Meta", "params": "3.2 B (Q5_K_M)",
            "key_points": "소형 멀티링궐 지시응답 특화, 1 B/3 B 버전, 요약·툴사용 튜닝"},
        "qwen-3-32b": {"display_name": "Qwen 3 32B", 
            "provider": "Alibaba Cloud", "params": "32.8 B",
            "key_points": "‘Thinking ↔ Non-Thinking’ 하이브리드 모드, 128 K 컨텍스트, 에이전트·추론 강화"},
        "gemma-3-27b-qat": {"display_name": "Gemma 3 27B-QAT", 
            "provider": "Google", "params": "27.4 B",
            "key_points": "멀티모달(텍스트+이미지), 128 K 컨텍스트, QAT 덕분에 단일 GPU 구동"},
        "deepseek-r1-distill-32b": {"display_name": "DeepSeek-R1 Distill 32B", 
            "provider": "DeepSeek", "params": "32 B",
            "key_points": "RL-기반 Reasoning R1 모델을 소형화, 32 K 컨텍스트, o1-mini급 성능"},
        "qwq-32b": {"display_name": "QwQ 32B", 
            "provider": "Alibaba Cloud", "params": "32.5 B",
            "key_points": "강화학습 Reasoning 특화, 131 K 컨텍스트, DeepSeek-R1과 경쟁"}
    }
    MODEL_TIPS = [
        "추론·코딩 문제 해결 → Qwen 3 235B, Qwen 3 32B, QwQ 32B, DeepSeek-R1 Distill 32B.",
        "한국어 질의응답 → SEOKDONG-Llama 3.1 (8B).",
        "RAG·검색 임베딩 → SFR-Embedding (Mistral-7B).",
        "리소스 제약 환경 → Llama 3.2 (3B Instruct), Gemma 3 27B-QAT (단일 GPU 가능)."
    ]
    # Prepare summaries for enriched info
    enriched = []
    for raw in raw_models:
        key = raw.lower()
        info = MODEL_SUMMARIES.get(key)
        entry = {"name": raw}
        if info:
            entry.update(info)
        else:
            entry.update({"display_name": raw, "provider": "", "params": "", "key_points": ""})
        enriched.append(entry)
    # Return raw model names for dropdown, enriched summaries for detailed table, and usage tips
    return {"models": raw_models, "summaries": enriched, "tips": MODEL_TIPS}

@router.post("/ollama/models/refresh")
def refresh_model_info(model_name: str = None):
    """
    모델 정보 캐시를 수동으로 새로고침합니다.
    
    Args:
        model_name: 특정 모델만 새로고침할 경우 모델 이름 (선택사항)
    """
    try:
        from app.services.model_info_service import refresh_model_cache
        
        if model_name:
            logger.info(f"Refreshing cache for specific model: {model_name}")
            refresh_model_cache(model_name)
            return {"message": f"모델 '{model_name}' 정보가 새로고침되었습니다."}
        else:
            logger.info("Refreshing cache for all models")
            refresh_model_cache()
            return {"message": "모든 모델 정보가 새로고침되었습니다."}
            
    except Exception as e:
        logger.error(f"Error refreshing model cache: {e}")
        raise HTTPException(status_code=500, detail=f"모델 정보 새로고침 실패: {str(e)}")

@router.get("/documents")
def list_documents():
    """
    DB에 저장된 모든 문서(document_id, chunk 개수, 미리보기 등) 목록을 반환합니다.
    """
    try:
        docs = get_all_documents()
        return {"documents": docs}
    except VectorDBError as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=f"문서 목록 조회 오류: {e.message}")

@router.delete("/documents/{document_id}")
def delete_document_by_id(document_id: str):
    """
    특정 문서 ID에 해당하는 문서를 DB와 파일 시스템에서 삭제합니다.
    """
    if not validate_document_id(document_id):
        raise HTTPException(status_code=400, detail=f"Invalid document ID: {document_id}")
    
    logger.info(f"Delete request for document: {document_id}")
    
    try:
        # 1. 벡터 DB에서 멀티모달 컨텐트 삭제
        db_deleted = delete_multimodal_document(document_id)
        
        # 2. 파일 시스템에서 삭제
        file_deleted = DocumentFileManager.delete_file_by_document_id(document_id)
        
        # 3. 처리 상태에서도 제거
        if document_id in pdf_processing_status:
            del pdf_processing_status[document_id]
        
        if db_deleted or file_deleted:
            logger.info(f"Successfully deleted document: {document_id} (DB: {db_deleted}, File: {file_deleted})")
            return {
                "message": f"Document {document_id} deleted successfully",
                "document_id": document_id,
                "deleted_from_db": db_deleted,
                "deleted_from_files": file_deleted
            }
        else:
            logger.warning(f"Document not found: {document_id}")
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
            
    except VectorDBError as e:
        logger.error(f"Vector DB error during delete: {e}")
        raise HTTPException(status_code=500, detail=f"DB 삭제 오류: {e.message}")
    except FileProcessingError as e:
        logger.error(f"File processing error during delete: {e}")
        raise HTTPException(status_code=500, detail=f"파일 삭제 오류: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error during delete: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="문서 삭제 중 예상치 못한 오류가 발생했습니다")

@router.delete("/documents")
def delete_all_documents_endpoint():
    """
    모든 문서를 DB와 파일 시스템에서 삭제합니다.
    """
    logger.info("Delete all documents request received")
    
    try:
        # 1. 기본 벡터 DB에서 모든 문서 삭제
        db_deleted_count = delete_all_documents()
        
        # 2. 멀티모달 컬렉션에서 모든 문서 삭제
        multimodal_deleted_count = delete_all_multimodal_documents()
        
        # 3. 파일 시스템에서 모든 파일 삭제
        file_deleted_count = DocumentFileManager.delete_all_files()
        
        # 4. 처리 상태 초기화
        pdf_processing_status.clear()
        
        logger.info(f"Successfully deleted all documents (Text DB: {db_deleted_count} docs, Multimodal DB: {multimodal_deleted_count} docs, Files: {file_deleted_count} files)")
        
        return {
            "message": "All documents deleted successfully",
            "deleted_documents_count": max(db_deleted_count, multimodal_deleted_count),
            "deleted_files_count": file_deleted_count,
            "text_db_count": db_deleted_count,
            "multimodal_db_count": multimodal_deleted_count
        }
        
    except VectorDBError as e:
        logger.error(f"Vector DB error during delete all: {e}")
        raise HTTPException(status_code=500, detail=f"DB 전체 삭제 오류: {e.message}")
    except FileProcessingError as e:
        logger.error(f"File processing error during delete all: {e}")
        raise HTTPException(status_code=500, detail=f"파일 전체 삭제 오류: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error during delete all: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="전체 문서 삭제 중 예상치 못한 오류가 발생했습니다")

@router.get("/documents/{document_id}")
def get_document_details(document_id: str):
    """
    특정 문서의 상세 정보를 반환합니다.
    """
    if not validate_document_id(document_id):
        raise HTTPException(status_code=400, detail=f"Invalid document ID: {document_id}")
    
    try:
        # DB에서 멀티모달 문서 정보 조회
        db_info = get_multimodal_document_info(document_id)
        if not db_info:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found in database")
        
        # 파일 시스템에서 파일 정보 조회
        file_info = DocumentFileManager.get_file_info(document_id)
        
        # 정보 통합
        result = {
            "document_id": document_id,
            "db_info": db_info,
            "file_info": file_info,
            "processing_status": pdf_processing_status.get(document_id)
        }
        
        return result
        
    except HTTPException as e:
        raise e
    except VectorDBError as e:
        logger.error(f"Vector DB error getting document details: {e}")
        raise HTTPException(status_code=500, detail=f"문서 정보 조회 오류: {e.message}")
    except FileProcessingError as e:
        logger.error(f"File processing error getting document details: {e}")
        raise HTTPException(status_code=500, detail=f"파일 정보 조회 오류: {e.message}")

@router.post("/documents/cleanup")
def cleanup_orphaned_files():
    """
    벡터 DB에 없는 고아 파일들을 정리합니다.
    """
    logger.info("Cleanup orphaned files request received")
    
    try:
        # 1. 벡터 DB에서 유효한 document_id 목록 가져오기
        documents = get_all_documents()
        valid_document_ids = [doc["document_id"] for doc in documents]
        
        # 2. 고아 파일 정리
        orphaned_count = DocumentFileManager.cleanup_orphaned_files(valid_document_ids)
        
        logger.info(f"Cleaned up {orphaned_count} orphaned files")
        
        return {
            "message": "Orphaned files cleanup completed",
            "cleaned_files_count": orphaned_count,
            "valid_documents_count": len(valid_document_ids)
        }
        
    except (VectorDBError, FileProcessingError) as e:
        logger.error(f"Error during cleanup: {e}")
        raise HTTPException(status_code=500, detail=f"정리 작업 오류: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error during cleanup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="정리 작업 중 예상치 못한 오류가 발생했습니다")

@router.get("/storage/stats")
def get_storage_statistics():
    """
    저장소 사용량 통계를 반환합니다.
    """
    try:
        # 파일 시스템 통계
        file_stats = DocumentFileManager.get_storage_stats()
        
        # 벡터 DB 통계
        documents = get_all_documents()
        total_chunks = sum(doc.get("chunk_count", 0) for doc in documents)
        
        return {
            "file_storage": file_stats,
            "vector_db": {
                "total_documents": len(documents),
                "total_chunks": total_chunks
            }
        }
        
    except (VectorDBError, FileProcessingError) as e:
        logger.error(f"Error getting storage stats: {e}")
        raise HTTPException(status_code=500, detail=f"통계 조회 오류: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error getting storage stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="통계 조회 중 예상치 못한 오류가 발생했습니다")

# ===== 환영메시지 API 엔드포인트들 =====

@router.get("/welcome-messages/random")
async def get_random_welcome_message():
    """
    랜덤 환영메시지를 반환합니다.
    """
    try:
        from app.services.welcome_message_service import get_random_welcome_message
        
        message = get_random_welcome_message()
        return {
            "message": message,
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting random welcome message: {e}")
        # 기본 메시지 반환
        return {
            "message": "📚 안녕하세요! 업로드된 문서들에 대해 궁금한 것이 있으시면 언제든 물어보세요.",
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }

@router.post("/welcome-messages/generate")
async def generate_welcome_messages(count: int = 5):
    """
    새로운 환영메시지들을 생성합니다.
    """
    try:
        from app.services.welcome_message_service import generate_welcome_messages
        
        if count < 1 or count > 10:
            raise HTTPException(status_code=400, detail="생성할 메시지 개수는 1-10개 사이여야 합니다")
        
        generated_count = generate_welcome_messages(count)
        
        return {
            "requested_count": count,
            "generated_count": generated_count,
            "message": f"환영메시지 {generated_count}개가 성공적으로 생성되었습니다.",
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating welcome messages: {e}")
        raise HTTPException(status_code=500, detail=f"환영메시지 생성 중 오류가 발생했습니다: {str(e)}")

@router.get("/welcome-messages")
async def get_all_welcome_messages():
    """
    모든 환영메시지 목록을 반환합니다.
    """
    try:
        from app.services.welcome_message_service import welcome_service
        
        messages = welcome_service.get_all_messages()
        doc_summary = welcome_service.get_document_summary()
        
        return {
            "messages": messages,
            "total_count": len(messages),
            "document_summary": doc_summary,
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting welcome messages: {e}")
        raise HTTPException(status_code=500, detail="환영메시지 목록 조회 중 오류가 발생했습니다")

@router.delete("/welcome-messages/{message_id}")
async def delete_welcome_message(message_id: int):
    """
    특정 환영메시지를 삭제합니다.
    """
    try:
        from app.services.welcome_message_service import welcome_service
        
        success = welcome_service.delete_message(message_id)
        
        if success:
            return {
                "message": f"환영메시지 ID {message_id}가 삭제되었습니다.",
                "timestamp": __import__('datetime').datetime.now().isoformat()
            }
        else:
            raise HTTPException(status_code=404, detail=f"환영메시지 ID {message_id}를 찾을 수 없습니다")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting welcome message {message_id}: {e}")
        raise HTTPException(status_code=500, detail="환영메시지 삭제 중 오류가 발생했습니다")

@router.get("/welcome-messages/stats")
async def get_welcome_message_stats():
    """
    환영메시지 통계 정보를 반환합니다.
    """
    try:
        from app.services.welcome_message_service import get_welcome_message_stats
        
        stats = get_welcome_message_stats()
        return stats
        
    except Exception as e:
        logger.error(f"Error getting welcome message stats: {e}")
        raise HTTPException(status_code=500, detail="환영메시지 통계 조회 중 오류가 발생했습니다")

