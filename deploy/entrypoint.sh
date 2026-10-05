#!/bin/sh
# lambda gives a fresh, writable /tmp only. the chroma index needs a writable
# copy, and onnxruntime writes a small telemetry file under $HOME/.cache, but the
# embedding model is only read, so it is linked from the image instead of copied
set -e

export HOME=/tmp/apphome
export CHROMA_PATH=/tmp/chroma_db

mkdir -p /tmp/apphome/.cache
cp -r /opt/apphome/.cache/Microsoft /tmp/apphome/.cache/ 2>/dev/null || true
ln -sfn /opt/apphome/.cache/chroma /tmp/apphome/.cache/chroma
[ -d "$CHROMA_PATH" ] || cp -r /app/chroma_db "$CHROMA_PATH"

(find /opt/apphome/.cache/chroma /usr/local/lib/python3.11/site-packages/onnxruntime -type f -exec cat {} + > /dev/null 2>&1 &)

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
