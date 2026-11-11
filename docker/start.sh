#!/bin/sh
set -e

cd /app/backend
# Um worker: o hub do change stream (SSE) e os limites de assinantes vivem em
# memória do processo (app/services/alerts.py); com 2+ workers cada um abriria
# o próprio change stream e o limite de conexões deixaria de valer.
uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1 &
nginx -g 'daemon off;'
