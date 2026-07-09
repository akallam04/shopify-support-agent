# Deployment

The backend runs as a container image on AWS Lambda (arm64 / Graviton) behind a Function URL. The frontend is static files on Vercel. Lambda scales to zero, so idle cost is nothing.

## What runs where

- **Backend**: the FastAPI app plus its stdio MCP server, packaged in one image. The AWS Lambda Web Adapter forwards Lambda invokes to uvicorn, so the app and its lifespan-managed MCP session run unchanged. The vector index and embedding model are baked into the image at build time; the entrypoint copies the index into the writable `/tmp` before serving, since Lambda's filesystem is read-only everywhere else.
- **Frontend**: `frontend/` is plain static files. Set its `api-base` meta tag to the Function URL and deploy to Vercel.

## Cost

- Lambda compute and the Function URL: free tier covers demo traffic (1M requests, 400k GB-seconds/month, always free).
- ECR image storage: about 1.2 GB, free for the first year (500 MB tier), then roughly $0.15/month.
- Vercel Hobby and Anthropic tokens (~$0.18 per 100 conversations): already accounted for.

Effectively $0/month. The one tradeoff is a cold start of a few seconds while the model loads after idle.

## Backend deploy (run from the repo root)

Set these once:

```
export AWS_REGION=us-east-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export REPO=aurora-support
export ECR=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO
```

1. Create the ECR repository (one time). Free:

   ```
   aws ecr create-repository --repository-name $REPO --region $AWS_REGION
   ```

2. Build the arm64 image and push it. This is the only slow step:

   ```
   aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR
   docker build --platform linux/arm64 -f deploy/Dockerfile -t $ECR:latest .
   docker push $ECR:latest
   ```

3. Create the Lambda execution role (one time). Free:

   ```
   aws iam create-role --role-name aurora-support-lambda \
     --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
   aws iam attach-role-policy --role-name aurora-support-lambda \
     --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
   ```

4. Create the function. 1024 MB memory, 60s timeout, credentials as env vars (fill in your real values):

   ```
   aws lambda create-function --function-name aurora-support \
     --package-type Image --code ImageUri=$ECR:latest \
     --role arn:aws:iam::$ACCOUNT_ID:role/aurora-support-lambda \
     --architectures arm64 --memory-size 1024 --timeout 60 \
     --environment "Variables={SHOPIFY_STORE_DOMAIN=...,SHOPIFY_ADMIN_TOKEN=...,SHOPIFY_API_VERSION=2026-01,ANTHROPIC_API_KEY=...,CORS_ORIGINS=https://YOUR-VERCEL-APP.vercel.app}" \
     --region $AWS_REGION
   ```

5. Add a public Function URL:

   ```
   aws lambda create-function-url-config --function-name aurora-support --auth-type NONE --region $AWS_REGION
   aws lambda add-permission --function-name aurora-support \
     --statement-id public-url --action lambda:InvokeFunctionUrl \
     --principal '*' --function-url-auth-type NONE --region $AWS_REGION
   ```

   The command prints `FunctionUrl`. That is the backend base URL.

6. Smoke test:

   ```
   curl -s <FunctionUrl>health
   curl -s -X POST <FunctionUrl>chat -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"do you have waterproof jackets?"}]}'
   ```

To ship a new build later: repeat step 2, then
`aws lambda update-function-code --function-name aurora-support --image-uri $ECR:latest --region $AWS_REGION`.

## Frontend deploy (Vercel)

1. Put the Function URL (without a trailing slash) into `frontend/index.html`:
   `<meta name="api-base" content="https://<FunctionUrl-host>" />`
2. In Vercel, import the repo, set the root directory to `frontend`, framework preset "Other", no build command.
3. After it deploys, copy the Vercel URL back into the Lambda `CORS_ORIGINS` env var (step 4) so the browser is allowed to call the backend:
   `aws lambda update-function-configuration --function-name aurora-support --environment "Variables={...,CORS_ORIGINS=https://<your>.vercel.app}" --region $AWS_REGION`

The Vercel URL is the live demo link.
