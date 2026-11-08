"""Cron entry: purge expired anonymous data once. `uv run python scripts/purge_anonymous.py`."""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "backend"))
os.environ["PURGE_INTERVAL_SECONDS"] = "0"  # no background loop inside the one-shot run
os.environ["PURGE_ON_START"] = "false"


async def main() -> int:
    from app.main import create_app, lifespan
    from app.services.cleanup import run_purge

    app = create_app()
    async with lifespan(
        app
    ):  # reuses the app wiring (registry, vectors, parents, BM25, objects)
        counts = await run_purge(app.state)
    print(counts)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
