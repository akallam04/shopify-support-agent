#!/bin/sh
# Creates the Lambda function, reading credentials from .env so no secrets are
# ever typed on the command line. Run from the repo root: sh deploy/create_function.sh
set -e

if [ ! -f .env ]; then
  echo "no .env in the current directory, run this from the repo root" >&2
  exit 1
fi

set -a
. ./.env
set +a

: "${SHOPIFY_STORE_DOMAIN:?missing in .env}"
: "${SHOPIFY_ADMIN_TOKEN:?missing in .env}"
: "${ANTHROPIC_API_KEY:?missing in .env}"

REGION=us-east-1
ECR=287871537333.dkr.ecr.us-east-1.amazonaws.com/aurora-support:latest
ROLE=arn:aws:iam::287871537333:role/aurora-support-lambda

aws lambda create-function \
  --function-name aurora-support \
  --package-type Image \
  --code "ImageUri=$ECR" \
  --role "$ROLE" \
  --architectures arm64 \
  --memory-size 1024 \
  --timeout 60 \
  --region "$REGION" \
  --environment "Variables={SHOPIFY_STORE_DOMAIN=$SHOPIFY_STORE_DOMAIN,SHOPIFY_ADMIN_TOKEN=$SHOPIFY_ADMIN_TOKEN,SHOPIFY_API_VERSION=${SHOPIFY_API_VERSION:-2026-01},ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY,CORS_ORIGINS=*}" \
  --query 'FunctionArn' --output text

echo "created, it will be Active in a minute or two"
