#!/bin/sh
set -eu

AWS_REGION="${AWS_REGION:-us-east-1}"
FUNCTION="${LAMBDA_FUNCTION:-aurora-support}"
SCHEDULE="${KEEP_WARM_SCHEDULE:-aurora-keep-warm}"
ROLE_NAME="${KEEP_WARM_ROLE:-aurora-keep-warm-scheduler}"
EVENT=deploy/keep-warm-event.json

if [ ! -f "$EVENT" ]; then
  echo "run this from the repo root: sh deploy/keep_warm.sh" >&2
  exit 1
fi

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
FUNCTION_ARN=$(aws lambda get-function --function-name "$FUNCTION" --region "$AWS_REGION" --query Configuration.FunctionArn --output text)
SCHEDULE_ARN="arn:aws:scheduler:$AWS_REGION:$ACCOUNT:schedule/default/$SCHEDULE"
ROLE_ARN="arn:aws:iam::$ACCOUNT:role/$ROLE_NAME"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/trust.json" <<JSON
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "scheduler.amazonaws.com"}, "Action": "sts:AssumeRole",
  "Condition": {"StringEquals": {"aws:SourceAccount": "$ACCOUNT", "aws:SourceArn": "$SCHEDULE_ARN"}}}]}
JSON
cat > "$WORK/invoke.json" <<JSON
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": ["$FUNCTION_ARN", "$FUNCTION_ARN:*"]}]}
JSON

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "file://$WORK/trust.json" \
    --description "Lets EventBridge Scheduler call the health check" >/dev/null
  echo "created role $ROLE_NAME"
  sleep 10
else
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "file://$WORK/trust.json"
fi
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name invoke-health-check --policy-document "file://$WORK/invoke.json"

FUNCTION_ARN="$FUNCTION_ARN" ROLE_ARN="$ROLE_ARN" EVENT="$EVENT" TARGET="$WORK/target.json" python3 -c '
import json, os
event = json.load(open(os.environ["EVENT"]))
target = {
    "Arn": os.environ["FUNCTION_ARN"],
    "RoleArn": os.environ["ROLE_ARN"],
    "Input": json.dumps(event),
    "RetryPolicy": {"MaximumRetryAttempts": 0, "MaximumEventAgeInSeconds": 60},
}
json.dump(target, open(os.environ["TARGET"], "w"))
'

ACTION=create-schedule
if aws scheduler get-schedule --name "$SCHEDULE" --region "$AWS_REGION" >/dev/null 2>&1; then
  ACTION=update-schedule
fi
aws scheduler "$ACTION" --name "$SCHEDULE" --region "$AWS_REGION" \
  --schedule-expression "rate(5 minutes)" --flexible-time-window Mode=OFF --state ENABLED \
  --description "Calls GET /health on the Lambda every 5 minutes; no model call" \
  --target "file://$WORK/target.json" >/dev/null
echo "$ACTION: $SCHEDULE calls GET /health on $FUNCTION every 5 minutes"
