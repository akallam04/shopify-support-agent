import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_frontend_logic_passes_its_node_tests() -> None:
    files = sorted(str(p) for p in Path("tests/js").glob("*.test.js"))
    result = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=60)
    assert files and result.returncode == 0, result.stdout + result.stderr
