"""Event-loop helper. Async psycopg (the pgvector library) cannot run on Windows' default ProactorEventLoop, so local
dev on Windows uses a SelectorEventLoop. Linux (containers, GKE) is unaffected."""

import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    return asyncio.SelectorEventLoop if sys.platform == "win32" else None


def run(main: Coroutine[Any, Any, T]) -> T:
    """asyncio.run with a psycopg-compatible loop on every platform."""
    with asyncio.Runner(loop_factory=loop_factory()) as runner:  # Runner: 3.11+ (asyncio.run gained it in 3.12)
        return runner.run(main)
