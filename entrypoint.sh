#!/bin/bash

# Crear archivo de control para descarga única
DOWNLOAD_FLAG="/tmp/models_downloaded"

# Ejecutar descarga de modelos solo si no se ha hecho antes
if [ ! -f "$DOWNLOAD_FLAG" ]; then
    echo "Descargando modelos..."
    python3 download_models.py
    if [ $? -eq 0 ]; then
        touch "$DOWNLOAD_FLAG"
        echo "Descarga completada."
    else
        echo "Error en la descarga de modelos."
        exit 1
    fi
else
    echo "Modelos ya descargados, omitiendo descarga."
fi

# Iniciar la aplicación principal con reinicio automático
echo "Iniciando aplicación..."
while true; do
    python3 adaptar.py
    EXIT_CODE=$?
    
    if [ $EXIT_CODE -eq 0 ]; then
        echo "Aplicación terminó normalmente."
        break
    else
        echo "Aplicación terminó con error (código $EXIT_CODE). Reiniciando en 5 segundos..."
        sleep 5
    fi
done