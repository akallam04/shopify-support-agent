#!/bin/sh
set -eu

AWS_REGION="${AWS_REGION:-us-east-1}"
FUNCTION="${LAMBDA_FUNCTION:-aurora-support}"
REPOSITORY="${ECR_REPOSITORY:-aurora-support}"
API_BASE="${API_BASE:-$(sed -n 's/.*name="api-base" content="\([^"]*\)".*/\1/p' frontend/index.html)}"

if [ ! -f deploy/Dockerfile ]; then
  echo "run this from the repo root: sh deploy/deploy.sh" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "commit first: images are tagged with the commit they were built from" >&2
  exit 1
fi

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
SHA=$(git rev-parse --short=12 HEAD)
IMAGE="$REGISTRY/$REPOSITORY:$SHA"

aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker buildx build --platform linux/arm64 --provenance=false -f deploy/Dockerfile --build-arg GIT_SHA="$SHA" -t "$IMAGE" --push .

aws lambda update-function-code --function-name "$FUNCTION" --image-uri "$IMAGE" --region "$AWS_REGION" >/dev/null
aws lambda wait function-updated --function-name "$FUNCTION" --region "$AWS_REGION"
echo "deployed $IMAGE"

for attempt in 1 2 3 4 5 6; do
  result=$(curl -s -o /dev/null -w "%{http_code} %{time_total}" --max-time 40 "$API_BASE/health") || true
  code=${result% *}
  echo "warm-up $attempt: HTTP $code in ${result#* }s"
  if [ "$code" = "200" ]; then
    exit 0
  fi
  sleep 5
done
echo "the new image did not answer /health after 6 tries" >&2
exit 1
