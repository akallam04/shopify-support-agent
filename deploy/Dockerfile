# Lambda container for the support agent. The web adapter lets the normal
# uvicorn app (with its lifespan-managed MCP session) run unchanged on Lambda.
FROM public.ecr.aws/docker/library/python:3.11-slim

# aws lambda web adapter, forwards lambda invokes to the http app on $PORT
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

ENV HOME=/root \
    PORT=8080 \
    CHROMA_PATH=/tmp/chroma_db \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY mcp_server ./mcp_server
COPY data ./data

# build the index at image-build time: downloads the embedding model into
# /root/.cache (baked, read at runtime) and writes chroma_db into /app
RUN python -m app.rag.index

COPY deploy/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

CMD ["./entrypoint.sh"]
