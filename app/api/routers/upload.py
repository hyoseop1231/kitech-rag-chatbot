"""
Upload router — PDF 업로드 및 백그라운드 처리
"""
from fastapi import APIRouter, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
import os
import uuid
import gc
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import settings
from app.utils.logging_config import get_logger
from app.utils.security import FileValidator
from app.utils.exceptions import OCRError, FileProcessingError
from app.services.ocr_service import extract_multimodal_content_from_pdf
from app.services.vector_db_service import delete_multimodal_document
from app.utils.file_manager import DocumentFileManager
from typing import List

logger = get_logger(__name__)

router = APIRouter()

# PDF 처리 상태 저장 (간단한 인메모리 방식)
pdf_processing_status = {}

executor = ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_FILE_PROCESSING)


def process_pdf_background_entry(
    file_path,
    document_id,
    filename: str,
    ocr_correction_enabled: bool,
    llm_correction_enabled: bool
):
    """백그라운드 처리용 엔트리 함수 (threaded)"""
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
        "timestamp": datetime.now().isoformat()
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
            "timestamp": datetime.now().isoformat()
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
                overall_progress = 10 + (page_progress * 40)
                
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
            _cleanup_failed_processing(file_path, document_id)
            return

        # 2-4. 세분화된 텍스트 처리 (청킹, 임베딩, 저장)
        logger.info(f"[Task {document_id}] Step 2-4: Processing text content...")
        
        try:
            from app.services.text_processing_service import split_text_into_chunks_with_progress, get_embeddings
            from app.services.vector_db_service import store_multimodal_content
            
            if extracted_text and extracted_text.strip():
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
                
                text_chunks = split_text_into_chunks_with_progress(
                    extracted_text,
                    progress_callback=detailed_chunking_callback,
                    total_pages=total_pages
                )
                
                update_status("Embedding", f"{len(text_chunks)}개 청크 임베딩 생성 중...", 65, total_pages, total_pages, 
                            {"chunks_count": len(text_chunks)})
                text_embeddings = get_embeddings(text_chunks)
                
                update_status("Metadata", "메타데이터 준비 중...", 75, total_pages, total_pages, {})
                text_metadatas = [
                    {"source_document_id": document_id, "filename": filename, "chunk_index": i, "content_type": "text"}
                    for i in range(len(text_chunks))
                ]
                
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
            _cleanup_failed_processing(file_path, document_id)
            return

        # 5. 최종 상태 업데이트
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
        
        # 완료 후 15초 후 상태 정리
        def cleanup_status():
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
        _cleanup_failed_processing(file_path, document_id)
    finally:
        gc.collect()


def _cleanup_failed_processing(file_path: str, document_id: str):
    """처리 실패 시 파일 및 관련 데이터 정리"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up failed upload file: {file_path}")
        
        content_dir = os.path.join(settings.UPLOAD_DIR, f"{document_id}_content")
        if os.path.exists(content_dir):
            import shutil
            shutil.rmtree(content_dir)
            logger.info(f"Cleaned up content directory: {content_dir}")
        
        try:
            delete_multimodal_document(document_id)
            logger.info(f"Cleaned up vector DB data for: {document_id}")
        except Exception as db_error:
            logger.warning(f"Failed to clean up vector DB for {document_id}: {db_error}")
        
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
    if len(files) > settings.MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=400, 
            detail=f"최대 {settings.MAX_FILES_PER_UPLOAD}개의 파일까지만 한 번에 업로드할 수 있습니다. (현재: {len(files)}개)"
        )
    
    logger.info(f"Processing {len(files)} files (max {settings.MAX_CONCURRENT_FILE_PROCESSING} concurrent)")
    results = []
    for file in files:
        logger.info(f"Upload request received for file: {file.filename}")
        if not file.filename:
            results.append({"filename": None, "error": "No filename provided"})
            continue
        content = await file.read()
        file_size = len(content)
        if file_size == 0:
            results.append({"filename": file.filename, "error": "Empty file uploaded"})
            continue
        document_id = f"{os.path.splitext(file.filename)[0]}_{str(uuid.uuid4())[:8]}"
        safe_filename = FileValidator.generate_safe_filename(file.filename, document_id)
        file_path = Path(settings.UPLOAD_DIR) / safe_filename
        try:
            with open(file_path, "wb") as buffer:
                buffer.write(content)
            logger.info(f"File saved: {file_path} ({file_size} bytes)")
            validation_result = FileValidator.validate_uploaded_file(file_path, file.filename, file_size)
            if not validation_result["is_valid"]:
                os.remove(file_path)
                logger.warning(f"File validation failed: {validation_result['errors']}")
                results.append({"filename": file.filename, "error": f"File validation failed: {'; '.join(validation_result['errors'])}"})
                continue
            active_tasks = len([status for status in pdf_processing_status.values() 
                              if status.get("step") not in ["Done", "Completed", "Error", "Queued"]])
            
            is_queued = active_tasks >= settings.MAX_CONCURRENT_FILE_PROCESSING
            
            initial_step = "Queued" if is_queued else "Preparing"
            initial_message = "처리 대기열에서 순서를 기다리는 중..." if is_queued else "문서 처리 준비 중..."
            
            pdf_processing_status[document_id] = {
                "step": initial_step,
                "message": initial_message,
                "percent": 0,
                "current_page": 0,
                "total_pages": 0,
                "details": {"queued": is_queued, "queue_position": active_tasks + 1 if is_queued else 0},
                "timestamp": datetime.now().isoformat()
            }
            logger.info(f"✅ Processing status initialized for document: {document_id} (queued: {is_queued})")
            
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


@router.get("/upload_status/{document_id}")
def get_upload_status(document_id: str):
    status = pdf_processing_status.get(document_id)
    if status:
        logger.debug(f"📊 Status check for {document_id}: {status['step']} - {status.get('percent', 0)}%")
        return status
    else:
        available_ids = list(pdf_processing_status.keys())
        logger.warning(f"⚠️ Status not found for document: {document_id}")
        logger.warning(f"📋 Available document IDs ({len(available_ids)}): {available_ids}")
        
        similar_ids = [id for id in available_ids if document_id in id or id in document_id]
        if similar_ids:
            logger.info(f"🔍 Similar IDs found: {similar_ids}")
        
        current_time = datetime.now()
        cleaned_count = 0
        
        for doc_id, doc_status in list(pdf_processing_status.items()):
            try:
                timestamp_str = doc_status.get('timestamp')
                if timestamp_str:
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00').replace('+00:00', ''))
                    age_minutes = (current_time - timestamp).total_seconds() / 60
                    
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
