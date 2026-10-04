#!/bin/sh
# Creates the Lambda function from the image in ECR. Run from the repo root: sh deploy/create_function.sh
# The AWS account is looked up at run time, the execution role name comes from .env, and the
# secrets reach AWS through a private temp file instead of the command line.
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
: "${LAMBDA_ROLE_NAME:?missing in .env}"

if [ -n "${SHOPIFY_WRITE_TOKEN:-}" ] && [ "$SHOPIFY_WRITE_TOKEN" = "$SHOPIFY_ADMIN_TOKEN" ]; then
  echo "SHOPIFY_ADMIN_TOKEN holds the write-test token, refusing to deploy it" >&2
  exit 1
fi

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
IMAGE="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/aurora-support:${IMAGE_TAG:-latest}"
ROLE="arn:aws:iam::$ACCOUNT:role/$LAMBDA_ROLE_NAME"

ENV_FILE=$(mktemp)
chmod 600 "$ENV_FILE"
trap 'rm -f "$ENV_FILE"' EXIT

ENV_FILE="$ENV_FILE" python3 -c '
import json, os
keys = ("SHOPIFY_STORE_DOMAIN", "SHOPIFY_ADMIN_TOKEN", "ANTHROPIC_API_KEY")
variables = {k: os.environ[k] for k in keys}
variables["SHOPIFY_API_VERSION"] = os.environ.get("SHOPIFY_API_VERSION") or "2026-10"
variables["CORS_ORIGINS"] = "*"
with open(os.environ["ENV_FILE"], "w") as fh:
    json.dump({"Variables": variables}, fh)
'

aws lambda create-function \
  --function-name aurora-support \
  --package-type Image \
  --code "ImageUri=$IMAGE" \
  --role "$ROLE" \
  --architectures arm64 \
  --memory-size 1024 \
  --timeout 60 \
  --region "$REGION" \
  --environment "file://$ENV_FILE" \
  --query 'FunctionArn' --output text

echo "created, it will be Active in a minute or two"
