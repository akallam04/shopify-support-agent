"""Copies named keys from .env into the Lambda's environment without printing any values.

Lambda replaces the whole variable map on every update, so this reads the current map,
merges in the requested keys, and writes it back through a private temp file rather than
the command line, where values would show up in the process list.

Run from the repo root: .venv/bin/python deploy/sync_lambda_env.py SHOPIFY_ADMIN_TOKEN [KEY ...]
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values

FUNCTION = "aurora-support"
REGION = "us-east-1"


def aws(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["aws", "lambda", *args, "--function-name", FUNCTION, "--region", REGION],
        capture_output=True,
        text=True,
    )


def scrub(text: str, secrets: list[str]) -> str:
    for value in secrets:
        if value:
            text = text.replace(value, "***")
    return text


def main(keys: list[str]) -> int:
    if not keys:
        print("name at least one key from .env to copy, values are never printed")
        return 2
    env = dotenv_values(Path(".env"))
    missing = [k for k in keys if not env.get(k)]
    if missing:
        print(f"not set in .env: {', '.join(missing)}")
        return 2

    current = aws("get-function-configuration", "--query", "Environment.Variables", "--output", "json")
    if current.returncode != 0:
        print("could not read the Lambda configuration, check your AWS credentials")
        return 1
    variables: dict[str, str] = json.loads(current.stdout) or {}
    secrets = list(variables.values()) + [env[k] for k in keys]

    changed = [k for k in keys if variables.get(k) != env[k]]
    if not changed:
        print(f"already in sync: {', '.join(keys)}")
        return 0
    variables.update({k: env[k] for k in changed})

    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"Variables": variables}, fh)
        updated = aws(
            "update-function-configuration",
            "--environment",
            f"file://{path}",
            "--query",
            "LastUpdateStatus",
            "--output",
            "text",
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if updated.returncode != 0:
        print("update failed: " + scrub(updated.stderr.strip(), secrets))
        return 1

    waited = subprocess.run(
        ["aws", "lambda", "wait", "function-updated", "--function-name", FUNCTION, "--region", REGION],
        capture_output=True,
        text=True,
    )
    state = "Successful" if waited.returncode == 0 else "still updating, check the console"
    print(f"updated {', '.join(changed)} (values not shown), update {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
