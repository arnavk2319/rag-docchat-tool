"""Run blocking (CPU/IO) work off the event loop."""

from collections.abc import Callable
from typing import Any

from starlette.concurrency import run_in_threadpool


async def run_blocking[T](fn: Callable[..., T], *args: Any) -> T:
    """Run fn(*args) in the worker threadpool and await the result."""
    return await run_in_threadpool(fn, *args)
