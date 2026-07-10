#!/bin/sh
# Restricts the backend to a single allowed origin, reading the other env vars
# from .env so no secrets are typed on the command line.
# Usage: sh deploy/update_cors.sh https://your-app.vercel.app
set -e

if [ ! -f .env ]; then
  echo "no .env in the current directory, run this from the repo root" >&2
  exit 1
fi

CORS="${1:?pass the allowed origin, e.g. https://shopify-support-agent.vercel.app}"

set -a
. ./.env
set +a

: "${SHOPIFY_STORE_DOMAIN:?missing in .env}"
: "${SHOPIFY_ADMIN_TOKEN:?missing in .env}"
: "${ANTHROPIC_API_KEY:?missing in .env}"

aws lambda update-function-configuration \
  --function-name aurora-support \
  --region us-east-1 \
  --environment "Variables={SHOPIFY_STORE_DOMAIN=$SHOPIFY_STORE_DOMAIN,SHOPIFY_ADMIN_TOKEN=$SHOPIFY_ADMIN_TOKEN,SHOPIFY_API_VERSION=${SHOPIFY_API_VERSION:-2026-01},ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY,CORS_ORIGINS=$CORS}" \
  --query 'LastUpdateStatus' --output text

echo "CORS restricted to $CORS"
