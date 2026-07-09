#!/bin/sh
# lambda's filesystem is read-only except /tmp, so stage the baked index there
# before chroma opens it for writes (sqlite side files)
set -e

if [ ! -d "$CHROMA_PATH" ]; then
  cp -r /app/chroma_db "$CHROMA_PATH"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
