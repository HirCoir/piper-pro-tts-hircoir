# 🎤 Piper TTS Playground

Un playground web interactivo para probar modelos de síntesis de voz Piper TTS con interfaz moderna y medidas de seguridad avanzadas.

## 🚀 Características

- **Interfaz Web Moderna**: Playground interactivo para probar diferentes voces
- **Múltiples Modelos**: Soporte para modelos Piper TTS en español (México, Argentina, España, USA)
- **Descarga Automática**: Descarga modelos desde Hugging Face, WebDAV o GitHub
- **Seguridad Avanzada**: Protección contra automatización y ataques
- **Procesamiento Inteligente**: Filtrado de texto y reemplazos automáticos
- **Verificación de Integridad**: Validación SHA256 de modelos descargados
- **Arquitectura Multi-plataforma**: Soporte para AMD64, ARM64 y ARMv7

## 📋 Requisitos

- Docker y Docker Compose
- Al menos 2GB de RAM
- Espacio en disco: 500MB-2GB (dependiendo de los modelos)

## 🛠️ Instalación Rápida


### Con Docker

```bash
docker run -d \
  --name piper-tts \
  -p 7860:7860 \
  -e REPO_HUGGINGFACE="tu-usuario/tu-repo" \
  -e TOKEN_HUGGINGFACE="tu-token" \
  -e USERS="admin,password123" \
  ghcr.io/yahirhub/piper-pro-tts-hircoir:latest
```

## 🔧 Variables de Entorno

### 📥 Descarga de Modelos (`download_models.py`)

#### Hugging Face
```bash
REPO_HUGGINGFACE="usuario/repositorio"    # Repositorio HF con modelos
TOKEN_HUGGINGFACE="hf_token_aqui"         # Token de autenticación HF
```

#### WebDAV
```bash
WEBDAV_URL="https://webdav.server.com/models/"  # URL del servidor WebDAV
WEBDAV_USER="usuario"                            # Usuario WebDAV
WEBDAV_PASSWORD="contraseña"                     # Contraseña WebDAV
```

#### GitHub
```bash
GITHUB_REPO="owner/repo"                   # Repositorio GitHub
GITHUB_PATH="models/"                      # Ruta dentro del repo (opcional)
GITHUB_TOKEN="ghp_token_aqui"             # Token GitHub (opcional, para repos privados)
```

#### Configuración General
```bash
MODELS_DIR="/home/app/models"              # Directorio de modelos (default)
```

### 🎯 Aplicación Principal (`app.py`)

#### Autenticación
```bash
USERS="user1,pass1|user2,pass2"           # Usuarios formato: user,pass|user,pass
```

#### Configuración de Seguridad
```bash
MAX_REQUESTS_PER_MINUTE=10                # Límite de requests por minuto
MAX_REQUESTS_PER_HOUR=100                 # Límite de requests por hora
BLOCK_DURATION_MINUTES=30                 # Duración de bloqueo temporal
MAX_TEXT_LENGTH=5000                      # Longitud máxima de texto
```

#### Configuración del Servidor
```bash
PIPER_HOST="0.0.0.0"                      # Host de la aplicación
PIPER_PORT="7860"                         # Puerto de la aplicación
```

### 🐳 Docker Build Arguments

```dockerfile
# Configuración de descarga
ARG TOKEN_HUGGINGFACE                     # Token HF para build time
ARG REPO_HUGGINGFACE                      # Repo HF para build time
ARG WEBDAV_URL                            # URL WebDAV para build time
ARG WEBDAV_USER                           # Usuario WebDAV para build time
ARG WEBDAV_PASSWORD                       # Password WebDAV para build time
ARG GITHUB_REPO                           # Repo GitHub para build time
ARG GITHUB_PATH                           # Path GitHub para build time
```

## 📁 Estructura del Proyecto

```
├── app.py                 # Aplicación principal Flask
├── download_models.py         # Script de descarga de modelos
├── entrypoint.sh             # Script de inicio del contenedor
├── requirements.txt          # Dependencias Python
├── Dockerfile               # Configuración Docker
├── global_replacements.json # Reemplazos de texto globales
├── modelos.json            # Configuración de modelos disponibles
├── templates/              # Plantillas HTML
│   ├── index.html         # Interfaz principal
│   ├── login.html         # Página de login
│   └── favicon.ico        # Icono del sitio
└── static/                # Archivos estáticos
    └── images/           # Imágenes de modelos
```

## 🔐 Características de Seguridad

### Rate Limiting
- **10 requests/minuto** por IP
- **100 requests/hora** por IP
- **Bloqueo temporal** de 30 minutos para IPs sospechosas

### Validación de User-Agent
- Bloquea herramientas automatizadas (`curl`, `wget`, `python-requests`, etc.)
- Permite solo navegadores legítimos
- Lista negra dinámica de User-Agents sospechosos

### Validación de Headers
- Detecta headers de automatización (`selenium-remote-control`, `webdriver`)
- Valida Content-Type apropiado
- Bloquea requests con headers sospechosos

### Validación de Contenido
- Límite de **5000 caracteres** por request
- Detecta intentos de inyección (`<script>`, `<?php>`, `javascript:`)
- Filtrado de contenido malicioso

### Protección IP
- Seguimiento de IPs a través de proxies (`X-Forwarded-For`, `X-Real-IP`)
- Exención de seguridad para IPs privadas/locales en desarrollo
- Logging detallado de actividad sospechosa

## 🔄 Procesamiento de Texto

### Reemplazos Automáticos
- **Números**: `1` → `uno`, `2` → `dos`, etc.
- **Símbolos**: `&` → `y`, `%` → `por ciento`
- **Abreviaciones**: `Sr.` → `Señor`, `Dr.` → `Doctor`
- **Unidades**: `km` → `kilómetros`, `°C` → `grados celsius`
- **Acrónimos**: `API` → `a pe i`, `URL` → `u erre ele`

### Filtrado Inteligente
- Eliminación de bloques de código
- Procesamiento de saltos de línea
- División inteligente de oraciones
- Normalización de puntuación

## 🚀 Uso

### Interfaz Web
1. Accede a `http://localhost:7860`
2. Inicia sesión con tus credenciales
3. Selecciona un modelo de voz
4. Ingresa el texto a sintetizar
5. Ajusta parámetros (speaker, noise_scale, etc.)
6. Haz clic en "Convertir"


## 🔍 Monitoreo y Logs

### Health Check
- Endpoint: `http://localhost:7860/`
- Intervalo: 30 segundos
- Timeout: 10 segundos
- Reintentos: 3

### Logs Importantes
```bash
# Ver logs del contenedor
docker logs piper-tts -f

# Logs de seguridad
grep "Rate limit\|Suspicious\|Invalid" logs/app.log

# Logs de descarga
grep "Descargando\|SHA256" logs/app.log
```

## 🛠️ Desarrollo

### Ejecutar Localmente
```bash
# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
export USERS="admin,password"
export REPO_HUGGINGFACE="tu-repo"

# Ejecutar aplicación
python app.py
```

### Construir Imagen Docker
```bash
# Build básico
docker build -t piper-tts .

# Build con argumentos
docker build \
  --build-arg TOKEN_HUGGINGFACE="tu-token" \
  --build-arg REPO_HUGGINGFACE="tu-repo" \
  -t piper-tts .
```

## 🔧 Solución de Problemas

### Modelos No Se Descargan
1. Verificar variables de entorno de descarga
2. Comprobar conectividad a internet
3. Validar tokens de autenticación
4. Revisar logs de `download_models.py`

### Error 403 - Forbidden
- Verificar User-Agent del navegador
- Comprobar si la IP está bloqueada temporalmente
- Revisar headers de la request

### Error 429 - Too Many Requests
- Esperar el tiempo de bloqueo (30 minutos)
- Reducir frecuencia de requests
- Verificar límites de rate limiting

### Modelos Corruptos
- Los archivos se verifican automáticamente con SHA256
- Eliminar archivos `.onnx` corruptos para re-descarga
- Verificar integridad de la red durante descarga