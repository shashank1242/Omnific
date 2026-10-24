"""Repo-wide pytest config. On Windows, run async tests on a SelectorEventLoop (async psycopg can't use Proactor)."""

import asyncio
import sys

import pytest

if sys.platform == "win32":

    @pytest.fixture(scope="session")
    def event_loop_policy():
        return asyncio.WindowsSelectorEventLoopPolicy()
