import os
import shutil
import logging
import datetime
import threading
import gc
from concurrent.futures import ThreadPoolExecutor

from app.config import settings
from app.utils.logging_config import get_logger
from app.services.ocr_service import extract_multimodal_content_from_pdf
from app.services.vector_db_service import store_multimodal_content, delete_multimodal_document
from app.utils.exceptions import OCRError, FileProcessingError

logger = get_logger(__name__)

# PDF processing status is managed here, away from the API layer.
pdf_processing_status = {}

# Thread pool for concurrent processing is also managed here.
executor = ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_FILE_PROCESSING)

def get_pdf_processing_status():
    """Returns the current status dictionary."""
    return pdf_processing_status

def get_executor():
    """Returns the thread pool executor instance."""
    return executor

def process_pdf_background_entry(
    file_path,
    document_id,
    filename: str,
    ocr_correction_enabled: bool,
    llm_correction_enabled: bool
):
    """
    Entry point for background PDF processing. This function is submitted to the thread pool.
    """
    process_pdf_background(
        file_path,
        document_id,
        filename,
        ocr_correction_enabled,
        llm_correction_enabled
    )

def _cleanup_failed_processing(file_path: str, document_id: str):
    """
    Cleans up files and data if processing fails.
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up failed upload file: {file_path}")

        content_dir = os.path.join(settings.UPLOAD_DIR, f"{document_id}_content")
        if os.path.exists(content_dir):
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


def process_pdf_background(
    file_path: str,
    document_id: str,
    filename: str,
    ocr_correction_enabled: bool,
    llm_correction_enabled: bool
):
    """
    The main background task to extract all content from a PDF and store it.
    """
    logger.info(f"🚀 Background task started: Processing PDF {document_id} from {file_path}")
    logger.info(f"Correction settings: OCR={ocr_correction_enabled}, LLM={llm_correction_enabled}")

    current_status = pdf_processing_status.get(document_id, {})
    was_queued = current_status.get("step") == "Queued"

    pdf_processing_status[document_id] = {
        "step": "Starting",
        "message": "큐에서 처리 시작됨" if was_queued else "백그라운드 처리 시작됨",
        "percent": 1,
        "current_page": 0,
        "total_pages": 0,
        "details": {"started": True, "was_queued": was_queued},
        "timestamp": datetime.datetime.now().isoformat()
    }
    logger.info(f"✅ Background processing status updated for: {document_id}")

    def update_status(step: str, message: str, percent: int, current_page: int = 0, total_pages: int = 0, details: dict = None):
        status = {
            "step": step,
            "message": message,
            "percent": percent,
            "current_page": current_page,
            "total_pages": total_pages,
            "details": details or {},
            "timestamp": datetime.datetime.now().isoformat()
        }
        pdf_processing_status[document_id] = status
        logger.info(f"[Task {document_id}] {step}: {message} ({percent}%)")

    try:
        update_status("Analyzing", "PDF 파일 분석 중...", 5)

        import fitz
        try:
            doc = fitz.open(file_path)
            total_pages = len(doc)
            doc.close()
            logger.info(f"[Task {document_id}] PDF has {total_pages} pages")
        except Exception as e:
            logger.warning(f"[Task {document_id}] Could not determine page count: {e}")
            total_pages = 0

        update_status("OCR", f"OCR 및 콘텐츠 추출 시작... (총 {total_pages}페이지)", 10, 0, total_pages)

        logger.info(f"[Task {document_id}] Step 1: Extracting multimodal content...")
        try:
            def ocr_progress_callback(current_page: int, total_pages: int, stage: str, custom_message: str = None):
                page_progress = (current_page / total_pages) if total_pages > 0 else 0
                overall_progress = 10 + (page_progress * 40)
                stage_messages = {
                    "text": f"텍스트 추출 중... ({current_page}/{total_pages} 페이지)",
                    "images": f"이미지 추출 중... ({current_page}/{total_pages} 페이지)",
                    "tables": f"표 추출 중... ({current_page}/{total_pages} 페이지)",
                    "table_processing": custom_message or f"표 처리 중... ({current_page}/{total_pages} 페이지)",
                }
                message = custom_message or stage_messages.get(stage, f"페이지 처리 중... ({current_page}/{total_pages})")
                details = {"stage": stage, "pages_processed": current_page}
                update_status("OCR", message, int(overall_progress), current_page, total_pages, details)

            content_data = extract_multimodal_content_from_pdf(
                file_path, document_id, ocr_correction_enabled, llm_correction_enabled, progress_callback=ocr_progress_callback
            )
            extracted_text = content_data.get('text', '')
            extracted_images = content_data.get('images', [])
            extracted_tables = content_data.get('tables', [])

            if not extracted_text and not extracted_images and not extracted_tables:
                raise OCRError("No content extracted from PDF", "EMPTY_EXTRACTION")

            extract_details = {
                "text_length": len(extracted_text), "images_count": len(extracted_images), "tables_count": len(extracted_tables),
                "pages_processed": total_pages
            }
            update_status("ExtractComplete", f"추출 완료! 텍스트: {len(extracted_text)}자, 이미지: {len(extracted_images)}개, 표: {len(extracted_tables)}개", 50, total_pages, total_pages, extract_details)
            logger.info(f"[Task {document_id}] Extracted: text={len(extracted_text)} chars, images={len(extracted_images)}, tables={len(extracted_tables)}")

        except (OCRError, FileProcessingError) as e:
            update_status("Error", f"OCR 오류: {e.message}", 0, 0, total_pages, {"error": str(e)})
            logger.error(f"[Task {document_id}] OCR error: {e}")
            _cleanup_failed_processing(file_path, document_id)
            return

        logger.info(f"[Task {document_id}] Step 2-4: Processing and storing content...")

        try:
            from app.services.text_processing_service import split_text_into_chunks_with_progress, get_embeddings

            if extracted_text and extracted_text.strip():
                def chunking_callback(step, message):
                    percents = {"preprocessing": 55, "splitting": 60, "correction": 62}
                    update_status(step, message, percents.get(step, 60), total_pages, total_pages)

                text_chunks = split_text_into_chunks_with_progress(extracted_text, progress_callback=chunking_callback, total_pages=total_pages)

                update_status("Embedding", f"{len(text_chunks)}개 청크 임베딩 생성 중...", 65, total_pages, total_pages)
                text_embeddings = get_embeddings(text_chunks)

                update_status("Metadata", "메타데이터 준비 중...", 75, total_pages, total_pages)
                text_metadatas = [{"source_document_id": document_id, "filename": filename, "chunk_index": i, "content_type": "text"} for i in range(len(text_chunks))]

                update_status("Storing", "벡터 데이터베이스에 저장 중...", 80, total_pages, total_pages)
                store_multimodal_content(document_id=document_id, content_data={"text_chunks": text_chunks, "images": extracted_images, "tables": extracted_tables}, text_vectors=text_embeddings, text_metadatas=text_metadatas)
                result = {"text_chunks_stored": len(text_chunks), "images_stored": len(extracted_images), "tables_stored": len(extracted_tables)}
            else:
                update_status("Storing", "이미지/표 데이터 저장 중...", 80, total_pages, total_pages)
                store_multimodal_content(document_id=document_id, content_data={"text_chunks": [], "images": extracted_images, "tables": extracted_tables}, text_vectors=[], text_metadatas=[])
                result = {"text_chunks_stored": 0, "images_stored": len(extracted_images), "tables_stored": len(extracted_tables)}

            logger.info(f"[Task {document_id}] Successfully processed and stored multimodal content")

        except Exception as e:
            update_status("Error", f"텍스트 처리 오류: {str(e)}", 75, total_pages, total_pages, {"error": str(e)})
            logger.error(f"[Task {document_id}] Text processing error: {e}")
            _cleanup_failed_processing(file_path, document_id)
            return

        final_message = f"처리 완료! 텍스트: {result.get('text_chunks_stored', 0)}청크, 이미지: {result.get('images_stored', 0)}개, 표: {result.get('tables_stored', 0)}개"
        update_status("Completed", final_message, 100, total_pages, total_pages, {"final_counts": result})
        logger.info(f"✅ [Task {document_id}] Successfully processed and stored content for: {document_id}")

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
        _cleanup_failed_processing(file_path, document_id)
    finally:
        gc.collect()
