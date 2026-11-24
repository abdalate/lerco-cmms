"""Tests run on a temporary database — never on the real cmms.db."""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="cmms_test_")
os.environ["CMMS_DB_PATH"] = os.path.join(_TMP, "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import database as db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    db.init_db()


@pytest.fixture
def today():
    from datetime import date
    return date.today()
