"""Write the app's OpenAPI spec to docs/openapi.json (deterministic; checked in, drift-tested)."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "backend"))
os.environ["ENABLE_DOCS"] = "true"
os.environ.setdefault("LLM_PROVIDER", "mock")


def render() -> str:
    from app.main import create_app

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    (ROOT / "docs" / "openapi.json").write_text(render())
