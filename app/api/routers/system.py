from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
import time
import requests

from app.config import settings
from app.utils.logging_config import get_logger
from app.utils.monitoring import get_monitor
from app.utils.file_manager import DocumentFileManager
from app.services.vector_db_service import get_all_documents
from app.utils.exceptions import VectorDBError, FileProcessingError

logger = get_logger(__name__)
router = APIRouter()

@router.get("/health")
async def health_check():
    """
    Performs a health check of the application and its dependencies.
    """
    monitor = get_monitor()
    health_status = monitor.check_health()
    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(content=health_status, status_code=status_code)

@router.get("/metrics")
async def get_metrics():
    """
    Retrieves application performance metrics.
    """
    monitor = get_monitor()
    return JSONResponse(content={"performance": monitor.get_stats(), "timestamp": time.time()})

@router.get("/storage/stats")
def get_storage_statistics():
    """
    Returns statistics about storage usage (files and vector DB).
    """
    try:
        file_stats = DocumentFileManager.get_storage_stats()
        documents = get_all_documents()
        total_chunks = sum(doc.get("chunk_count", 0) for doc in documents)

        return {
            "file_storage": file_stats,
            "vector_db": {"total_documents": len(documents), "total_chunks": total_chunks}
        }
    except (VectorDBError, FileProcessingError) as e:
        logger.error(f"Error getting storage stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/ollama/status")
def ollama_status():
    """
    Checks the status of the Ollama server.
    """
    try:
        url = settings.OLLAMA_API_URL.replace('/api/generate', '')
        response = requests.get(url, timeout=settings.OLLAMA_TIMEOUT)
        response.raise_for_status()
        return {"status": "running", "url": url}
    except requests.exceptions.RequestException as e:
        logger.error(f"Ollama connection failed: {e}")
        return {"status": "unreachable", "url": url, "error": str(e)}
