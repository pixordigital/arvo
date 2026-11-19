"""Test harness: engines are cached per event loop, so tests get a fresh pool.

Clearing the cache between tests keeps memory bounded when the test suite
creates many short-lived loops.
"""

import pytest


@pytest.fixture(autouse=True)
def _fresh_db_engine():
    import app.database.engine as db

    db._engines.clear()
    db._sessions.clear()
    db._default_engine = None
    db._default_session = None
    yield
    db._engines.clear()
    db._sessions.clear()
    db._default_engine = None
    db._default_session = None
