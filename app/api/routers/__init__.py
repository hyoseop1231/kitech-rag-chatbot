"""
KITECH RAG Chatbot API Routers
모듈화된 API 라우터 패키지
"""
from fastapi import APIRouter

from app.api.routers.upload import router as upload_router
from app.api.routers.chat import router as chat_router
from app.api.routers.documents import router as documents_router
from app.api.routers.models import router as models_router
from app.api.routers.welcome import router as welcome_router
from app.api.routers.system import router as system_router

api_router = APIRouter()

api_router.include_router(upload_router, tags=["Upload"])
api_router.include_router(chat_router, tags=["Chat"])
api_router.include_router(documents_router, tags=["Documents"])
api_router.include_router(models_router, tags=["Models"])
api_router.include_router(welcome_router, tags=["Welcome"])
api_router.include_router(system_router, tags=["System"])
