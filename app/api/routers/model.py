from fastapi import APIRouter, HTTPException
import requests

from app.config import settings
from app.utils.logging_config import get_logger
from app.services.model_info_service import get_cached_model_info, refresh_model_cache

logger = get_logger(__name__)
router = APIRouter()

def _fallback_model_list():
    """Provides a fallback list of models if the service fails."""
    try:
        url = settings.OLLAMA_API_URL.replace('/api/generate', '/api/tags')
        response = requests.get(url, timeout=settings.OLLAMA_TIMEOUT)
        response.raise_for_status()
        models_data = response.json()
        model_names = [m['name'] for m in models_data.get('models', [])]
        return {"models": model_names, "summaries": [], "tips": []}
    except Exception as e:
        logger.error(f"Ollama fallback model list failed: {e}")
        return {"models": [], "summaries": [], "tips": [], "detail": str(e)}

@router.get("/ollama/models")
def ollama_models(force_refresh: bool = False):
    """
    Returns a list of available Ollama models, using a cache.
    """
    try:
        model_data = get_cached_model_info(force_refresh=force_refresh)
        if not model_data.get("models"):
            logger.warning("Primary model fetch failed, using fallback.")
            return _fallback_model_list()
        return model_data
    except Exception as e:
        logger.error(f"Error fetching model info: {e}", exc_info=True)
        return _fallback_model_list()

@router.post("/ollama/models/refresh")
def refresh_model_info_endpoint(model_name: str = None):
    """
    Manually triggers a refresh of the model information cache.
    """
    try:
        refresh_model_cache(model_name)
        return {"message": f"Model cache refresh triggered for: {'all models' if not model_name else model_name}."}
    except Exception as e:
        logger.error(f"Error refreshing model cache: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to refresh model cache.")
