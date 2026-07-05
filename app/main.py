"""FastAPI entrypoint. The /chat endpoint lands here in the agent phase."""

from fastapi import FastAPI

app = FastAPI(title="Shopify Support Agent")


@app.get("/health")
def health() -> dict:
    # liveness check, also what the deploy health probe will hit
    return {"status": "ok"}
