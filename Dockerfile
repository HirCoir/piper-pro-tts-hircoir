# Multi-stage build for Piper TTS API
FROM python:3.11-slim as base

# =============================================================================
# BUILD ARGUMENTS - Variables disponibles durante la construcción de la imagen
# =============================================================================
# Hugging Face Configuration
ARG TOKEN_HUGGINGFACE     # Token de autenticación para Hugging Face Hub
ARG REPO_HUGGINGFACE      # Repositorio HF en formato "usuario/repo" (ej: "microsoft/DialoGPT-medium")

# WebDAV Server Configuration  
ARG WEBDAV_URL            # URL del servidor WebDAV (ej: "https://webdav.server.com/models/")
ARG WEBDAV_USER           # Usuario para autenticación WebDAV
ARG WEBDAV_PASSWORD       # Contraseña para autenticación WebDAV

# GitHub Repository Configuration
ARG GITHUB_REPO           # Repositorio GitHub en formato "owner/repo" (ej: "rhasspy/piper")
ARG GITHUB_PATH           # Ruta dentro del repo (ej: "models/" o "" para raíz)
ARG GITHUB_TOKEN          # Token de GitHub para repos privados (opcional)

# =============================================================================
# ENVIRONMENT VARIABLES - Variables disponibles en tiempo de ejecución
# =============================================================================
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOWNLOAD_URL_BASE=https://github.com/rhasspy/piper/releases/download/2023.11.14-2/ \
    MODELS_DIR=/home/app/models \
    TEMP_AUDIO_DIR=/home/app/temp_audio

# Variables de configuración adicionales que se pueden sobrescribir:
# - PIPER_API_TOKEN: Token para autenticación de la API (default: "123")
# - PIPER_HOST: Host donde escucha la API (default: "0.0.0.0") 
# - PIPER_PORT: Puerto donde escucha la API (default: "7860")

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create application user and directories
RUN useradd -m -u 1000 app && \
    mkdir -p /home/app/models /home/app/temp_audio && \
    chown -R app:app /home/app

# Set working directory
WORKDIR /home/app


# Copy application files
COPY --chown=app:app adaptar.py .
COPY --chown=app:app download_models.py .
COPY --chown=app:app entrypoint.sh .
COPY --chown=app:app templates ./templates
COPY --chown=app:app global_replacements.json .
COPY --chown=app:app Dockerfile .
COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install "huggingface_hub[cli]"

# Download and extract Piper binaries based on architecture
RUN dpkgArch="$(dpkg --print-architecture)" && \
    case "${dpkgArch##*-}" in \
        amd64) DOWNLOAD_URL=${DOWNLOAD_URL_BASE}piper_linux_x86_64.tar.gz ;; \
        armhf) DOWNLOAD_URL=${DOWNLOAD_URL_BASE}piper_linux_armv7l.tar.gz ;; \
        arm64) DOWNLOAD_URL=${DOWNLOAD_URL_BASE}piper_linux_aarch64.tar.gz ;; \
        *) echo "Unsupported architecture: ${dpkgArch}"; exit 1 ;; \
    esac && \
    curl -SL ${DOWNLOAD_URL} | tar -xzC ./ && \
    chown -R app:app ./piper


# Set up Hugging Face token if provided
RUN if [ -n "$TOKEN_HUGGINGFACE" ]; then \
        mkdir -p /root/.cache/huggingface && \
        echo "$TOKEN_HUGGINGFACE" > /root/.cache/huggingface/token && \
        mkdir -p /home/app/.cache/huggingface && \
        echo "$TOKEN_HUGGINGFACE" > /home/app/.cache/huggingface/token && \
        chown -R app:app /home/app/.cache; \
    fi

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# =============================================================================
# HEALTH CHECK - Monitoreo automático del estado del contenedor
# =============================================================================
# El healthcheck permite a Docker/Kubernetes verificar si la aplicación está funcionando correctamente.
# - Se ejecuta cada 30 segundos para verificar el estado
# - Si falla 3 veces consecutivas, marca el contenedor como "unhealthy"
# - Útil para reiniciar automáticamente contenedores que no responden
# - Los orquestadores (Docker Swarm, Kubernetes) pueden usar esta info para balanceo de carga
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:7860/ || exit 1

# Expose port
EXPOSE 7860

# Switch to app user for security
USER app

# Set entrypoint
ENTRYPOINT ["./entrypoint.sh"]
