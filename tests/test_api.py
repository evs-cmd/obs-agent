import pytest
from fastapi.testclient import TestClient

from src.api.models import AskRequest


def test_ask_request_valid():
    req = AskRequest(message="Why is checkout slow?")
    assert req.message == "Why is checkout slow?"


def test_ask_request_empty():
    with pytest.raises(Exception):
        AskRequest(message="")


def test_health():
    from src.api.app import app
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
