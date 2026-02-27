from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import uvicorn
import os
from app.config import settings
from app.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup & shutdown events"""
    logger.info("Application startup complete - embedding model will load on first request")
    yield
    logger.info("Application shutting down")


# Create FastAPI app instance
app = FastAPI(
    title="KITECH RAG Chatbot",
    description="PDF-based RAG chatbot for foundry technology",
    version="3.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

# Security middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Add security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    # Add security headers to prevent XSS and other attacks
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self'; "
        "connect-src 'self' *; "
        "object-src 'none'; "
        "base-uri 'self'"
    )
    return response

# Trust hosts based on external access settings
# ENABLE_EXTERNAL_ACCESS=true → 모든 호스트 허용 (TrustedHostMiddleware 비활성화)
if settings.ENABLE_EXTERNAL_ACCESS:
    logger.info("🌐 External access enabled — TrustedHostMiddleware disabled")
elif not settings.DEBUG:
    app.add_middleware(
        TrustedHostMiddleware, 
        allowed_hosts=["localhost", "127.0.0.1", settings.HOST]
    )

# Mount static files directory (for CSS, JS)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Mount uploads directory for serving extracted images and tables
if os.path.exists("uploads"):
    app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Setup Jinja2 templates
templates = Jinja2Templates(directory="app/templates")

# Include modular API routers
from app.api.routers import api_router
app.include_router(api_router, prefix="/api")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Root endpoint to serve the main HTML page."""
    return templates.TemplateResponse("index.html", {"request": request, "title": "KITECH RAG Chatbot"})

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=settings.HOST,  # 기본값 0.0.0.0 (외부 접속 허용)
        port=settings.PORT,
    )
