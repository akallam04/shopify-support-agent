# Deployment

The backend runs as a container image on AWS Lambda (arm64 / Graviton) behind an API Gateway HTTP API. The frontend is static files on Vercel. Lambda scales to zero, so idle cost is nothing.

## What runs where

- **Backend**: the FastAPI app plus its stdio MCP server, packaged in one image. The AWS Lambda Web Adapter forwards invokes to uvicorn, so the app and its lifespan-managed MCP session run unchanged. The vector index and embedding model are baked into the image under a world-readable path at build time; the entrypoint stages both into `/tmp` (Lambda's only writable path) before serving.
- **Public entry**: an API Gateway HTTP API in front of the Lambda. A Lambda Function URL would be simpler, but brand-new AWS accounts block public Function URLs (`AuthType NONE` returns `403 Forbidden` regardless of the resource policy), and there is no per-account switch to disable that. API Gateway HTTP API is a separate public-endpoint path that is not subject to that block. The app needs no changes; the Web Adapter handles the API Gateway payload identically.
- **Frontend**: `frontend/` is plain static files. Its `api-base` meta tag points at the API Gateway URL; deploy to Vercel.

## Cost

- Lambda compute: free tier covers demo traffic (1M requests, 400k GB-seconds/month, always free).
- API Gateway HTTP API: free for 1M requests/month for the first 12 months, then $1.00/million.
- ECR image storage: about 1.2 GB, free for the first year (500 MB tier), then roughly $0.15/month.
- Vercel Hobby and Anthropic tokens (~$0.18 per 100 conversations): already accounted for.

Effectively $0/month at demo scale. The one tradeoff is a cold start of a few seconds while the model loads after idle; the frontend retries a cold-start 503 transparently.

## Backend deploy (run from the repo root)

Set these once:

```
export AWS_REGION=us-east-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/aurora-support
```

1. Create the ECR repository (one time, free):

   ```
   aws ecr create-repository --repository-name aurora-support --region $AWS_REGION
   ```

2. Build the arm64 image and push it. `--provenance=false` is required, otherwise buildx pushes a multi-manifest index that Lambda rejects with "image manifest ... not supported":

   ```
   aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR
   docker buildx build --platform linux/arm64 --provenance=false -f deploy/Dockerfile -t $ECR:latest --push .
   ```

3. Create the Lambda execution role (one time, free):

   ```
   aws iam create-role --role-name aurora-support-lambda \
     --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
   aws iam attach-role-policy --role-name aurora-support-lambda \
     --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
   ```

4. Create the function. `deploy/create_function.sh` reads the credentials from `.env` so no secrets are typed on the command line:

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

To ship a new build later: repeat step 2, then
`aws lambda update-function-code --function-name aurora-support --image-uri $ECR:latest --region $AWS_REGION`.

## Frontend deploy (Vercel)

1. Put the API Gateway URL (no trailing slash) into `frontend/index.html`:
   `<meta name="api-base" content="https://<api-id>.execute-api.us-east-1.amazonaws.com" />`
2. In Vercel, import the repo, set the root directory to `frontend`, framework preset "Other", no build command.
3. After it deploys, restrict the backend to the Vercel origin so only the demo page can call it:
   `sh deploy/create_function.sh` sets `CORS_ORIGINS=*`; update it with
   `aws lambda update-function-configuration --function-name aurora-support --environment "Variables={...,CORS_ORIGINS=https://<your>.vercel.app}" --region us-east-1`

The Vercel URL is the live demo link.
