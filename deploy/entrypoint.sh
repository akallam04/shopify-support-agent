#!/bin/sh
# lambda gives a fresh, writable /tmp only, so stage the baked index and the
# embedding model there and point HOME at the staged cache before chroma opens
# anything (chroma tries to write into $HOME/.cache on first embed)
set -e

export HOME=/tmp/apphome
export CHROMA_PATH=/tmp/chroma_db

mkdir -p /tmp/apphome/.cache
cp -r /opt/apphome/.cache/. /tmp/apphome/.cache/ 2>/dev/null || true
[ -d "$CHROMA_PATH" ] || cp -r /app/chroma_db "$CHROMA_PATH"

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
