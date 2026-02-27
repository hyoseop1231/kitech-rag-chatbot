"""
Documents router — 문서 CRUD 및 관리
"""
from fastapi import APIRouter, HTTPException

from app.config import settings
from app.utils.logging_config import get_logger
from app.utils.security import validate_document_id
from app.utils.file_manager import DocumentFileManager
from app.utils.exceptions import VectorDBError, FileProcessingError
from app.services.vector_db_service import (
    get_all_documents, delete_multimodal_document,
    get_multimodal_document_info, delete_all_documents, delete_all_multimodal_documents
)
from app.api.routers.upload import pdf_processing_status

logger = get_logger(__name__)

router = APIRouter()


@router.get("/documents")
def list_documents():
    """DB에 저장된 모든 문서 목록을 반환합니다."""
    try:
        docs = get_all_documents()
        return {"documents": docs}
    except VectorDBError as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=f"문서 목록 조회 오류: {e.message}")


@router.delete("/documents/{document_id}")
def delete_document_by_id(document_id: str):
    """특정 문서 ID에 해당하는 문서를 DB와 파일 시스템에서 삭제합니다."""
    if not validate_document_id(document_id):
        raise HTTPException(status_code=400, detail=f"Invalid document ID: {document_id}")
    
    logger.info(f"Delete request for document: {document_id}")
    
    try:
        db_deleted = delete_multimodal_document(document_id)
        file_deleted = DocumentFileManager.delete_file_by_document_id(document_id)
        
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
    """모든 문서를 DB와 파일 시스템에서 삭제합니다."""
    logger.info("Delete all documents request received")
    
    try:
        db_deleted_count = delete_all_documents()
        multimodal_deleted_count = delete_all_multimodal_documents()
        file_deleted_count = DocumentFileManager.delete_all_files()
        
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
    """특정 문서의 상세 정보를 반환합니다."""
    if not validate_document_id(document_id):
        raise HTTPException(status_code=400, detail=f"Invalid document ID: {document_id}")
    
    try:
        db_info = get_multimodal_document_info(document_id)
        if not db_info:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found in database")
        
        file_info = DocumentFileManager.get_file_info(document_id)
        
        result = {
            "document_id": document_id,
            "db_info": db_info,
            "file_info": file_info,
            "processing_status": pdf_processing_status.get(document_id)
        }
        
        return result
        
    except HTTPException:
        raise
    except VectorDBError as e:
        logger.error(f"Vector DB error getting document details: {e}")
        raise HTTPException(status_code=500, detail=f"문서 정보 조회 오류: {e.message}")
    except FileProcessingError as e:
        logger.error(f"File processing error getting document details: {e}")
        raise HTTPException(status_code=500, detail=f"파일 정보 조회 오류: {e.message}")


@router.post("/documents/cleanup")
def cleanup_orphaned_files():
    """벡터 DB에 없는 고아 파일들을 정리합니다."""
    logger.info("Cleanup orphaned files request received")
    
    try:
        documents = get_all_documents()
        valid_document_ids = [doc["document_id"] for doc in documents]
        
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
