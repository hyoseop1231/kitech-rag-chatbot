"""
Models router — Ollama 모델 관리 (비동기 httpx 사용)
"""
from fastapi import APIRouter, HTTPException
import json
from pathlib import Path

import httpx

from app.config import settings
from app.utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()

# 모델 카탈로그 로드
_CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "model_catalog.json"

def _load_model_catalog():
    """model_catalog.json에서 모델 정보를 로드합니다."""
    try:
        with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load model catalog: {e}")
        return {"summaries": {}, "tips": []}


@router.get("/ollama/status")
async def ollama_status():
    """Ollama 서버가 실행 중인지 확인합니다."""
    base = settings.OLLAMA_API_URL
    endpoints = [
        base.replace('/api/generate', '/api/models'),
        base.replace('/api/generate', '/api/tags'),
    ]
    last_detail = None
    
    async with httpx.AsyncClient(timeout=settings.OLLAMA_TIMEOUT) as client:
        for url in endpoints:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return {"status": "running", "endpoint": url}
                last_detail = resp.text
            except Exception as e:
                last_detail = str(e)
                continue
    
    return {
        "status": "unreachable",
        "detail": last_detail or "no response",
        "hint": "Ollama 서버가 실행 중인지 확인하세요 (예: 'ollama serve') 또는 OLLAMA_API_URL 설정을 검토하세요."
    }


@router.get("/ollama/models")
async def ollama_models(force_refresh: bool = False, quick: bool = True):
    """
    Ollama에 다운로드된 모델 목록을 반환합니다.
    """
    if quick:
        try:
            fallback_result = await _fallback_model_list()
            if fallback_result.get("models"):
                logger.info(f"Quick response: returning {len(fallback_result['models'])} models")
                return fallback_result
        except Exception as e:
            logger.warning(f"Quick fallback failed: {e}")
    
    try:
        import threading
        
        def background_cache_update():
            try:
                from app.services.model_info_service import get_cached_model_info
                get_cached_model_info(force_refresh=force_refresh)
                logger.info("Background model info cache update completed")
            except Exception as e:
                logger.error(f"Background cache update failed: {e}")
        
        if not quick:
            from app.services.model_info_service import get_cached_model_info
            
            logger.info(f"Fetching model info (force_refresh={force_refresh})")
            model_data = get_cached_model_info(force_refresh=force_refresh)
            
            if model_data.get("models"):
                logger.info(f"Returning {len(model_data['models'])} models with cached info")
                return model_data
        else:
            if not force_refresh:
                thread = threading.Thread(target=background_cache_update, daemon=True)
                thread.start()
        
        return await _fallback_model_list()
        
    except Exception as e:
        logger.error(f"Error fetching model info: {e}")
        return await _fallback_model_list()


async def _fallback_model_list():
    """폴백: 기본 모델 목록 반환 (비동기 httpx 사용)"""
    base = settings.OLLAMA_API_URL
    urls = [
        base.replace('/api/generate', '/api/models'),
        base.replace('/api/generate', '/api/tags'),
    ]
    raw_models = []
    detail = None
    
    async with httpx.AsyncClient(timeout=settings.OLLAMA_TIMEOUT) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    detail = resp.text
                    continue
                data = resp.json()
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
            return {
                "models": [],
                "summaries": [],
                "detail": detail or 'no response',
                "hint": "Ollama 서버가 실행 중인지 확인하세요 (예: 'ollama serve') 또는 OLLAMA_API_URL 설정을 검토하세요."
            }
    
    # 모델 카탈로그에서 정보 로드
    catalog = _load_model_catalog()
    model_summaries = catalog.get("summaries", {})
    model_tips = catalog.get("tips", [])
    
    enriched = []
    for raw in raw_models:
        key = raw.lower()
        info = model_summaries.get(key)
        entry = {"name": raw}
        if info:
            entry.update(info)
        else:
            entry.update({"display_name": raw, "provider": "", "params": "", "key_points": ""})
        enriched.append(entry)
    
    return {"models": raw_models, "summaries": enriched, "tips": model_tips}


@router.post("/ollama/models/refresh")
def refresh_model_info(model_name: str = None):
    """모델 정보 캐시를 수동으로 새로고침합니다."""
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
