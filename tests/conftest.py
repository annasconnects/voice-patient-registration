import os
import tempfile

# Point the app at a throwaway SQLite DB *before* app modules are imported.
_tmp = tempfile.mkdtemp()
# Set TEST_DATABASE_URL to run the suite against Postgres instead.
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["SEED_DATA"] = "true"
os.environ.pop("VAPI_SECRET", None)
os.environ.pop("API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    seed_if_empty()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


VALID = {
    "first_name": "Maria",
    "last_name": "Davis",
    "date_of_birth": "03/05/1990",
    "sex": "Female",
    "phone_number": "(415) 555-2671",
    "address_line_1": "1 Market St",
    "address_line_2": "Suite 200",
    "city": "San Francisco",
    "state": "CA",
    "zip_code": "94105",
}


def tool_call(name, args, call_id="call-1", tool_id="tc-1"):
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id, "customer": {"number": "+14155552671"}},
            "toolCallList": [{"id": tool_id, "type": "function", "function": {"name": name, "arguments": args}}],
        }
    }
