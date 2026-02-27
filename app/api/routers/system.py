"""
System router — 헬스체크, 메트릭, 저장소 통계
"""
from fastapi import APIRouter, HTTPException
import time

from app.utils.logging_config import get_logger
from app.utils.monitoring import get_monitor
from app.utils.file_manager import DocumentFileManager
from app.utils.exceptions import VectorDBError, FileProcessingError
from app.services.vector_db_service import get_all_documents

logger = get_logger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    monitor = get_monitor()
    health_status = monitor.check_health()
    
    from fastapi.responses import JSONResponse
    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(content=health_status, status_code=status_code)


@router.get("/metrics")
async def get_metrics():
    """Get application performance metrics"""
    monitor = get_monitor()
    stats = monitor.get_stats()
    
    from fastapi.responses import JSONResponse
    return JSONResponse(content={
        "performance": stats,
        "timestamp": time.time()
    })


@router.get("/storage/stats")
def get_storage_statistics():
    """저장소 사용량 통계를 반환합니다."""
    try:
        file_stats = DocumentFileManager.get_storage_stats()
        
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
