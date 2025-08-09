from fastapi import APIRouter, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from typing import List
import os
import uuid
from pathlib import Path

from app.config import settings
from app.utils.logging_config import get_logger
from app.utils.security import FileValidator, validate_document_id
from app.utils.file_manager import DocumentFileManager
from app.services.vector_db_service import get_all_documents, delete_multimodal_document, delete_all_multimodal_documents, get_multimodal_document_info, delete_all_documents
from app.utils.exceptions import FileProcessingError, VectorDBError

# Import the new service
from app.services.document_processing_service import (
    get_pdf_processing_status,
    get_executor,
    process_pdf_background_entry,
)

logger = get_logger(__name__)
router = APIRouter()

@router.post("/upload_pdf/", status_code=202)
async def upload_pdf(
    files: List[UploadFile] = File(...),
    ocr_correction_enabled: bool = Form(False),
    llm_correction_enabled: bool = Form(False)
):
    f"""
    Uploads multiple PDF files for processing. Each file is processed in the background.
    A maximum of {settings.MAX_CONCURRENT_FILE_PROCESSING} files can be processed concurrently.
    """
    if len(files) > settings.MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {settings.MAX_FILES_PER_UPLOAD} files allowed per upload. (Got: {len(files)})"
        )

    pdf_processing_status = get_pdf_processing_status()
    executor = get_executor()
    results = []

    for file in files:
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

            validation_result = FileValidator.validate_uploaded_file(file_path, file.filename, file_size)
            if not validation_result["is_valid"]:
                os.remove(file_path)
                logger.warning(f"File validation failed: {validation_result['errors']}")
                results.append({"filename": file.filename, "error": f"File validation failed: {'; '.join(validation_result['errors'])}"})
                continue

            active_tasks = len([s for s in pdf_processing_status.values() if s.get("step") not in ["Completed", "Error", "Queued"]])
            is_queued = active_tasks >= settings.MAX_CONCURRENT_FILE_PROCESSING

            initial_step = "Queued" if is_queued else "Preparing"
            initial_message = "Waiting in the processing queue..." if is_queued else "Preparing for processing..."

            pdf_processing_status[document_id] = {
                "step": initial_step, "message": initial_message, "percent": 0, "current_page": 0, "total_pages": 0,
                "details": {"queued": is_queued}, "timestamp": __import__('datetime').datetime.now().isoformat()
            }

            executor.submit(
                process_pdf_background_entry,
                file_path, document_id, file.filename, ocr_correction_enabled, llm_correction_enabled
            )

            results.append({
                "message": "File uploaded successfully. Processing started in the background.",
                "filename": file.filename, "document_id": document_id,
                "file_hash": validation_result["file_hash"]
            })
        except Exception as e:
            if os.path.exists(file_path):
                os.remove(file_path)
            logger.error(f"Error during file upload: {e}", exc_info=True)
            results.append({"filename": file.filename, "error": "Could not save or start processing file"})

    return JSONResponse(content={"results": results})


@router.get("/upload_status/{document_id}")
def get_upload_status(document_id: str):
    """
    Retrieves the processing status of a specific document.
    """
    pdf_processing_status = get_pdf_processing_status()
    status = pdf_processing_status.get(document_id)
    if status:
        return status

    # If not in memory, it might be completed and cleaned up. Check DB.
    try:
        db_info = get_multimodal_document_info(document_id)
        if db_info:
            return {"step": "Completed", "message": "Processing is complete.", "percent": 100, "details": db_info}
    except VectorDBError:
        pass # Not found in DB either

    return {"step": "Unknown", "message": "Status not found for the given document ID.", "percent": 0}


@router.get("/documents")
def list_documents():
    """
    Lists all documents stored in the vector database.
    """
    try:
        return {"documents": get_all_documents()}
    except VectorDBError as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/documents/{document_id}")
def delete_document_by_id(document_id: str):
    """
    Deletes a specific document from the database and file system.
    """
    if not validate_document_id(document_id):
        raise HTTPException(status_code=400, detail=f"Invalid document ID format: {document_id}")

    try:
        db_deleted = delete_multimodal_document(document_id)
        file_deleted = DocumentFileManager.delete_file_by_document_id(document_id)

        pdf_processing_status = get_pdf_processing_status()
        if document_id in pdf_processing_status:
            del pdf_processing_status[document_id]

        if not db_deleted and not file_deleted:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")

        return {"message": f"Document {document_id} deleted successfully.", "db_deleted": db_deleted, "file_deleted": file_deleted}
    except (VectorDBError, FileProcessingError) as e:
        logger.error(f"Error deleting document {document_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/documents")
def delete_all_documents_endpoint():
    """
    Deletes all documents from the database and file system.
    """
    try:
        db_count = delete_all_multimodal_documents()
        # Also clear the old collection if it exists
        try:
            delete_all_documents()
        except Exception:
            pass # Ignore if old collection doesn't exist
        file_count = DocumentFileManager.delete_all_files()

        pdf_processing_status = get_pdf_processing_status()
        pdf_processing_status.clear()

        return {"message": "All documents deleted successfully.", "deleted_db_docs": db_count, "deleted_files": file_count}
    except (VectorDBError, FileProcessingError) as e:
        logger.error(f"Error deleting all documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/documents/{document_id}")
def get_document_details(document_id: str):
    """
    Retrieves detailed information about a specific document.
    """
    if not validate_document_id(document_id):
        raise HTTPException(status_code=400, detail=f"Invalid document ID format: {document_id}")

    try:
        db_info = get_multimodal_document_info(document_id)
        if not db_info:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found in database")

        file_info = DocumentFileManager.get_file_info(document_id)
        pdf_processing_status = get_pdf_processing_status()

        return {
            "document_id": document_id, "db_info": db_info, "file_info": file_info,
            "processing_status": pdf_processing_status.get(document_id)
        }
    except (VectorDBError, FileProcessingError) as e:
        logger.error(f"Error getting document details for {document_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/documents/cleanup")
def cleanup_orphaned_files():
    """
    Cleans up orphaned files that are not tracked in the vector database.
    """
    try:
        documents = get_all_documents()
        valid_ids = {doc["document_id"] for doc in documents}
        cleaned_count = DocumentFileManager.cleanup_orphaned_files(valid_ids)
        return {"message": "Orphaned files cleanup completed.", "cleaned_files_count": cleaned_count}
    except (VectorDBError, FileProcessingError) as e:
        logger.error(f"Error during file cleanup: {e}")
        raise HTTPException(status_code=500, detail=str(e))
