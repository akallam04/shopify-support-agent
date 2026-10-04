#!/bin/sh
# Restricts the backend to one allowed origin and leaves every other variable as it is.
# Usage: sh deploy/update_cors.sh https://your-app.vercel.app
set -e
CORS="${1:?pass the allowed origin, e.g. https://shopify-support-agent.vercel.app}"
.venv/bin/python deploy/sync_lambda_env.py "CORS_ORIGINS=$CORS"
