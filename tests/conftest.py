import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_user() -> dict:
    return {"uid": "test-uid", "email": "test@example.com", "role": "patient"}
