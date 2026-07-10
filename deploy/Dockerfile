# Lambda container for the support agent. The web adapter lets the normal
# uvicorn app (with its lifespan-managed MCP session) run unchanged on Lambda.
FROM public.ecr.aws/docker/library/python:3.11-slim

# aws lambda web adapter, forwards lambda invokes to the http app on $PORT
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

# HOME under /opt so the embedding model bakes into a world-readable path, since
# lambda only gives us /tmp as writable and may not run the container as root
ENV HOME=/opt/apphome \
    PORT=8080 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY mcp_server ./mcp_server
COPY data ./data

# bake the index into /app/chroma_db and the embedding model into $HOME/.cache,
# both world-readable so the entrypoint can stage them into /tmp at cold start
RUN mkdir -p /opt/apphome \
    && CHROMA_PATH=/app/chroma_db python -m app.rag.index \
    && chmod -R a+rX /opt/apphome /app/chroma_db

COPY deploy/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

CMD ["./entrypoint.sh"]
