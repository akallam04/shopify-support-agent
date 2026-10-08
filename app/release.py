"""Release manifest: which code, prompts, knowledge, models, and gate settings produced an answer."""

import hashlib
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent
PROMPT_FILES = ("app/agent/prompts.py",)
KNOWLEDGE_FILES = ("data/policies", "data/catalog/products.jsonl", "data/sim/seed.json")


@lru_cache
def _digest(paths: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    for name in paths:
        path = ROOT / name
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        for f in files:
            h.update(str(f.relative_to(ROOT)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def git_sha() -> str:
    return os.environ.get("GIT_SHA", "")[:12] or _git_head()


@lru_cache
def _git_head() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def release_manifest(settings: Settings) -> dict[str, Any]:
    parts = {
        "git_sha": git_sha(),
        "prompts": _digest(PROMPT_FILES),
        "knowledge": _digest(KNOWLEDGE_FILES),
        "models": {"router": settings.router_model, "answer": settings.answer_model},
        "thinking": settings.model_thinking,
        "gate": {
            "write_actions": settings.write_actions,
            "mutation_gate": settings.mutation_gate,
            "reflection": settings.gate_reflection,
            "confirmation": settings.gate_confirmation,
        },
    }
    release = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:12]
    return {"release": release, **parts}
