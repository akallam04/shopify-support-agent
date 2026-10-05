# Deployment

The backend runs as a container image on AWS Lambda (arm64 / Graviton) behind an API Gateway HTTP API. The frontend is static files on Vercel. Lambda scales to zero, so idle cost is nothing.

## What runs where

- **Backend**: the FastAPI app, packaged in one image. In the deployed demo it runs in sandbox mode (`API_MODE=sandbox`): each visitor gets a private copy of the store, rebuilt per request from the frozen seed in `data/sim/seed.json` plus the changes listed in a signed session token, so write actions never reach the real store. In live mode (`API_MODE=live`, the default) it answers read-only from the live store through its stdio MCP server, as v1 did. The AWS Lambda Web Adapter forwards invokes to uvicorn, so the app and its lifespan-managed MCP session run unchanged. The vector index and embedding model are baked into the image under a world-readable path at build time; the entrypoint stages both into `/tmp` (Lambda's only writable path) before serving.
- **Public entry**: an API Gateway HTTP API in front of the Lambda. A Lambda Function URL would be simpler, but brand-new AWS accounts block public Function URLs (`AuthType NONE` returns `403 Forbidden` regardless of the resource policy), and there is no per-account switch to disable that. API Gateway HTTP API is a separate public-endpoint path that is not subject to that block. The app needs no changes; the Web Adapter handles the API Gateway payload identically.
- **Frontend**: `frontend/` is plain static files. Its `api-base` meta tag points at the API Gateway URL; deploy to Vercel.

## Cost

- Lambda compute: free tier covers demo traffic (1M requests, 400k GB-seconds/month, always free).
- API Gateway HTTP API: free for 1M requests/month for the first 12 months, then $1.00/million.
- ECR image storage: about 1.2 GB, free for the first year (500 MB tier), then roughly $0.15/month.
- Vercel Hobby and Anthropic tokens (~$0.18 per 100 conversations): already accounted for.

Effectively $0/month at demo scale. The one tradeoff is a cold start of a few seconds after idle; the frontend retries a cold-start 503 transparently. Right after a new image is deployed, the first cold starts are slower while Lambda caches the image.

## Backend deploy (run from the repo root)

Set these once:

```
export AWS_REGION=us-east-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/aurora-support
export LAMBDA_ROLE_NAME=aurora-support-lambda
```

`LAMBDA_ROLE_NAME` also goes in `.env`, where `deploy/create_function.sh` reads it.

1. Create the ECR repository (one time, free):

   ```
   aws ecr create-repository --repository-name aurora-support --region $AWS_REGION
   ```

2. Build the arm64 image, tagged with the git commit it was built from, and push it. `--provenance=false` is required, otherwise buildx pushes a multi-manifest index that Lambda rejects with "image manifest ... not supported". `GIT_SHA` lets the release manifest name its commit inside the container:

   ```
   export SHA=$(git rev-parse --short=12 HEAD)
   aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR
   docker buildx build --platform linux/arm64 --provenance=false -f deploy/Dockerfile --build-arg GIT_SHA=$SHA -t $ECR:$SHA --push .
   ```

   Expire untagged images automatically (one time). The rule in `deploy/ecr-lifecycle.json` touches untagged images only, so the tagged image the function runs is never deleted. Preview it first with `aws ecr start-lifecycle-policy-preview`:

   ```
   aws ecr put-lifecycle-policy --repository-name aurora-support --region $AWS_REGION \
     --lifecycle-policy-text file://deploy/ecr-lifecycle.json
   ```

3. Create the Lambda execution role (one time, free):

   ```
   aws iam create-role --role-name $LAMBDA_ROLE_NAME \
     --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
   aws iam attach-role-policy --role-name $LAMBDA_ROLE_NAME \
     --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
   ```

4. Create the function. `deploy/create_function.sh` looks up the account ID, reads the credentials and role name from `.env`, and hands the secrets to AWS through a private temp file, so they never appear on a command line or in the process list:

   ```
   sh deploy/create_function.sh
   ```

5. Put an API Gateway HTTP API in front of it and allow it to invoke the Lambda:

   ```
   API_ID=$(aws apigatewayv2 create-api --name aurora-support-api --protocol-type HTTP \
     --target arn:aws:lambda:$AWS_REGION:$ACCOUNT_ID:function:aurora-support \
     --query ApiId --output text --region $AWS_REGION)
   aws lambda add-permission --function-name aurora-support --statement-id apigw-invoke \
     --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
     --source-arn "arn:aws:execute-api:$AWS_REGION:$ACCOUNT_ID:$API_ID/*/*" --region $AWS_REGION
   echo "https://$API_ID.execute-api.$AWS_REGION.amazonaws.com"
   ```

   That URL is the backend base.

6. Smoke test (the first call cold-starts, allow ~15s):

   ```
   curl -s <api-url>/health
   curl -s -X POST <api-url>/chat -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"do you have waterproof jackets?"}]}'
   ```

To ship a new build later: repeat step 2, then deploy by the commit tag, never by a moving tag:
`aws lambda update-function-code --function-name aurora-support --image-uri $ECR:$SHA --region $AWS_REGION`.

### Sandbox mode settings

The demo needs three extra variables. Generate `SESSION_SIGNING_KEY` once into `.env` (for example with `python -c "import secrets; print(secrets.token_urlsafe(48))"` written straight into the file), then push all three without printing values:

```
.venv/bin/python deploy/sync_lambda_env.py SESSION_SIGNING_KEY API_MODE=sandbox WRITE_ACTIONS=true
```

`sync_lambda_env.py` refuses `SHOPIFY_WRITE_TOKEN` and `SIM_USER_API_KEY` under any name, so the function only ever holds the read-only Shopify token. After a deploy, check that:

- `aws lambda get-function-configuration` lists no `SHOPIFY_WRITE_TOKEN`, and the admin token matches the read-only one (compare hashes, not values);
- `GET /health` returns the new `release`;
- a cancel confirmed in one chat shows up in that chat only, and the real store's order is unchanged.

Every request writes one JSON line to CloudWatch with a trace id, the release, per-node timings, tokens, cost, and any writes, and no message text.

## Cold start

Measured on the live function by forcing new execution environments (a description change), timing a cold `GET /health` and then a first chat, and reading `Init Duration` from the Lambda logs:

| | v1 (2 samples) | v2 as first deployed (2) | Model copy removed (7) | Plus background prefetch (7) |
|---|---|---|---|---|
| Init duration | 4.0 to 4.9s | 4.2s and 10s, the init limit | 2.2 to 6.1s | 3.0 to 5.5s, median 4.0s |
| First retrieval on a cold environment | not measured | up to 8.4s | 0.4 to 24s, two of seven over 20s | 0.75 to 1.1s |
| First chat | 2.9 to 3.4s | 4.5 to 11.5s | 3.7 to 26.9s | 3.5 to 4.3s |

The cost was Lambda loading container image layers lazily: on a host without the image cached, the embedding model's bytes were fetched on demand. The entrypoint used to copy all 167 MB of model files into `/tmp` during init, which sometimes pushed init past its 10 second limit. It now links the model read-only from the image, copies only onnxruntime's small telemetry folder, drops the unused 80 MB archive at build time, and reads the model and onnxruntime in the background as the container starts, so that fetch overlaps startup instead of the first question. All of it is free. Warm chats take about 3 to 3.5 seconds, most of it in the two model calls.

## Request limits

The public API is rate limited so a flood of requests cannot run up model costs:

```
aws apigatewayv2 update-stage --api-id <api-id> --stage-name '$default' --region us-east-1 \
  --default-route-settings 'ThrottlingBurstLimit=5,ThrottlingRateLimit=0.5,DetailedMetricsEnabled=false'
```

- **API Gateway throttling:** a burst of 5 requests, then 0.5 requests per second sustained.
  AWS enforces HTTP API throttling on a best-effort basis, so it slows a flood rather than
  cutting it off at an exact count.
- **Lambda concurrency:** capped by the account limit of 10 concurrent executions. Reserved
  concurrency cannot be set lower, because AWS requires at least 10 to stay unreserved.
- **Request size:** each message is capped at 4,000 characters, the history at 20 messages, and the whole conversation at 16,000 characters per request.
- **Per session:** a demo session stops at 60,000 model tokens and asks the visitor to start a new conversation. Sessions expire after 30 idle minutes.

Throttled responses (API Gateway 429, Lambda 503) come back without CORS headers, so the
browser sees them as network errors. The frontend therefore treats throttles, concurrency
rejections, and network errors the same way: two retries with exponential backoff, then a
"busy, please try again" message with a Try again button.

## Frontend deploy (Vercel)

1. Put the API Gateway URL (no trailing slash) into `frontend/index.html`:
   `<meta name="api-base" content="https://<api-id>.execute-api.us-east-1.amazonaws.com" />`
2. In Vercel, import the repo, set the root directory to `frontend`, framework preset "Other", no build command.
3. After it deploys, restrict the backend to the Vercel origin so only the demo page can call it.
   `sh deploy/create_function.sh` sets `CORS_ORIGINS=*`; tighten it with
   `sh deploy/update_cors.sh https://<your>.vercel.app`, which changes only that variable.

To rotate a credential later, update `.env` and run
`.venv/bin/python deploy/sync_lambda_env.py <KEY>`. It merges the key into the function's
existing variables and never prints values.

The Vercel URL is the live demo link.
