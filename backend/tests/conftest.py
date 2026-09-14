import os
import tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="photo-curator-tests-")
os.environ["PC_EXTENSION_IDS"] = "a" * 32
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models import Base
from app.db import engine, init_db
from app.config import settings
from app.security import local_token, sessions

@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    init_db()
    settings.live_trash_enabled = False
    sessions.clear()
    yield

@pytest.fixture
def client():
    with TestClient(app, base_url="http://localhost:8077") as c:
        c.headers["Authorization"] = f"Bearer {local_token()}"
        yield c
