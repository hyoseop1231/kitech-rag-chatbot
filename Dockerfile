# ===================================================================
# KITECH RAG Chatbot - Optimized Multi-stage Docker Build
# 한국생산기술연구원 멀티모달 RAG 챗봇 시스템
# ===================================================================

# ===================================================================
# BUILD STAGE: Dependencies and Build Tools
# ===================================================================
FROM python:3.13-slim as builder

# Build-time metadata
LABEL maintainer="KITECH AI Team <ai-team@kitech.re.kr>"
LABEL description="KITECH RAG Chatbot - Builder Stage"
LABEL version="2.0.0"

# Build environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100 \
    PIP_PROGRESS_BAR=off

# Create build user for security
RUN groupadd -r builder && useradd -r -g builder builder

# Install build dependencies and system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Build essentials
    build-essential \
    gcc \
    g++ \
    make \
    cmake \
    pkg-config \
    # Python development headers
    python3-dev \
    # System libraries for ML/CV
    libgl1-mesa-dev \
    libglib2.0-dev \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libgfortran5 \
    # OCR dependencies
    tesseract-ocr \
    tesseract-ocr-kor \
    tesseract-ocr-eng \
    libtesseract-dev \
    # Image processing libraries
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libwebp-dev \
    # OpenCV dependencies
    libopencv-dev \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    libv4l-dev \
    # Networking and security
    curl \
    wget \
    ca-certificates \
    gnupg \
    # Cleanup
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /tmp/* \
    && rm -rf /var/tmp/*

# Create virtual environment
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Upgrade pip and install wheel
RUN pip install --no-cache-dir --upgrade \
    pip==24.3.1 \
    setuptools==75.6.0 \
    wheel==0.45.1

# Copy requirements first for better Docker layer caching
COPY requirements.txt /tmp/requirements.txt

# Install Python dependencies in virtual environment
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ===================================================================
# RUNTIME STAGE: Minimal Production Image
# ===================================================================
FROM python:3.13-slim as runtime

# Runtime metadata
LABEL maintainer="KITECH AI Team <ai-team@kitech.re.kr>"
LABEL description="KITECH Multimodal RAG Chatbot System"
LABEL version="2.0.0"
LABEL org.opencontainers.image.title="KITECH RAG Chatbot"
LABEL org.opencontainers.image.description="Korean Foundry Technology Expert AI Assistant"
LABEL org.opencontainers.image.vendor="Korea Institute of Industrial Technology"
LABEL org.opencontainers.image.source="https://github.com/KITECH-AI/rag-chatbot"

# Runtime environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive \
    # Application settings
    APP_HOME=/app \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    # Performance optimizations
    MALLOC_TRIM_THRESHOLD=100000 \
    MALLOC_MMAP_THRESHOLD=64000 \
    MALLOC_MMAP_MAX=65536 \
    # Security settings
    PYTHONSAFEPATH=1

# Install only runtime dependencies (much smaller than build dependencies)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Essential system packages\
    ca-certificates \
    curl \
    # OCR runtime (without dev packages)\
    tesseract-ocr \
    tesseract-ocr-kor \
    tesseract-ocr-eng \
    # Runtime libraries for ML/CV (without dev packages)\
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    libgfortran5 \
    # Image processing runtime libraries\
    libjpeg62-turbo \
    libpng16-16 \
    libtiff6 \
    libwebp7 \
    # OpenCV runtime libraries\
    libopencv-core406 \
    libopencv-imgproc406 \
    libopencv-imgcodecs406 \
    # System utilities\
    procps \
    htop \
    # File type detection\
    file \
    libmagic1 \
    # Cleanup\
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /tmp/* \
    && rm -rf /var/tmp/* \
    && rm -rf /usr/share/doc/* \
    && rm -rf /usr/share/man/* \
    && rm -rf /usr/share/info/*

# Create application user and group with specific UID/GID for consistency
RUN groupadd -r -g 1000 appuser && \
    useradd -r -u 1000 -g appuser -d $APP_HOME -s /bin/bash appuser

# Set working directory
WORKDIR $APP_HOME

# Copy virtual environment from builder stage
COPY --from=builder --chown=appuser:appuser $VIRTUAL_ENV $VIRTUAL_ENV

# Copy application code with proper ownership
COPY --chown=appuser:appuser . .

# Create necessary directories with proper permissions
RUN mkdir -p \
    $APP_HOME/uploads \
    $APP_HOME/vector_db_data \
    $APP_HOME/logs \
    $APP_HOME/temp \
    $APP_HOME/cache \
    && chown -R appuser:appuser $APP_HOME \
    && chmod -R 755 $APP_HOME \
    && chmod -R 777 $APP_HOME/uploads \
    && chmod -R 777 $APP_HOME/vector_db_data \
    && chmod -R 777 $APP_HOME/logs \
    && chmod -R 777 $APP_HOME/temp \
    && chmod -R 777 $APP_HOME/cache

# Install additional security tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    dumb-init \
    tini \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Security: Remove setuid/setgid binaries to prevent privilege escalation
RUN find / -xdev -perm -4000 -exec chmod u-s {} + 2>/dev/null || true \
    && find / -xdev -perm -2000 -exec chmod g-s {} + 2>/dev/null || true

# Switch to non-root user
USER appuser

# Expose application port
EXPOSE 8000

# Add health check with comprehensive testing
HEALTHCHECK --interval=30s \
    --timeout=10s \
    --start-period=40s \
    --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Volume declarations for persistent data
VOLUME ["$APP_HOME/uploads", "$APP_HOME/vector_db_data", "$APP_HOME/logs"]

# ===================================================================
# CONTAINER STARTUP CONFIGURATION
# ===================================================================

# Development target (override for development)
FROM runtime as development
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    vim \
    nano \
    git \
    ssh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
USER appuser

# Set default environment for development
ENV DEBUG=true \
    LOG_LEVEL=DEBUG \
    RELOAD=true

# Development command with hot reload
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--log-level", "debug"]

# ===================================================================
# PRODUCTION STARTUP (Default)
# ===================================================================

# Multi-process production configuration
FROM runtime as production

# Production environment variables
ENV DEBUG=false \
    LOG_LEVEL=INFO \
    RELOAD=false \
    # Gunicorn configuration
    WORKERS=4 \
    WORKER_CLASS=uvicorn.workers.UvicornWorker \
    WORKER_CONNECTIONS=1000 \
    MAX_REQUESTS=1000 \
    MAX_REQUESTS_JITTER=100 \
    TIMEOUT=300 \
    KEEPALIVE=5 \
    # Application optimization
    PRELOAD_APP=true \
    ACCESS_LOG_FORMAT='%(h)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Production startup with dumb-init for proper signal handling
ENTRYPOINT ["dumb-init", "--"]

# Default production command with optimized Gunicorn settings
CMD ["sh", "-c", "gunicorn app.main:app \
    --bind 0.0.0.0:8000 \
    --workers ${WORKERS} \
    --worker-class ${WORKER_CLASS} \
    --worker-connections ${WORKER_CONNECTIONS} \
    --max-requests ${MAX_REQUESTS} \
    --max-requests-jitter ${MAX_REQUESTS_JITTER} \
    --timeout ${TIMEOUT} \
    --keep-alive ${KEEPALIVE} \
    --preload \
    --access-logfile - \
    --error-logfile - \
    --access-logformat '${ACCESS_LOG_FORMAT}' \
    --log-level ${LOG_LEVEL}"]

# ===================================================================
# METADATA AND DOCUMENTATION
# ===================================================================

# Environment variable documentation
ENV DOCS_ENV_VARS="\
DEBUG=false|true - Enable debug mode \n\
SECRET_KEY=<secret> - Application secret key \n\
OLLAMA_API_URL=<url> - Ollama API endpoint \n\
OLLAMA_DEFAULT_MODEL=<model> - Default LLM model \n\
MAX_FILE_SIZE=<size> - Maximum upload file size in MB \n\
OCR_LANGUAGES=<langs> - OCR language settings \n\
LOG_LEVEL=INFO|DEBUG|WARNING|ERROR - Logging level \n\
WORKERS=<number> - Number of Gunicorn workers \n\
CORS_ORIGINS=<origins> - Allowed CORS origins"

# Build information
ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.created=$BUILD_DATE \
      org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.title="KITECH RAG Chatbot" \
      org.opencontainers.image.description="AI-powered foundry technology expert assistant" \
      org.opencontainers.image.authors="KITECH AI Team" \
      org.opencontainers.image.vendor="Korea Institute of Industrial Technology" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.documentation="https://github.com/KITECH-AI/rag-chatbot/README.md"