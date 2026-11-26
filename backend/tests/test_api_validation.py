"""Input validation and rate limiting via the real FastAPI app (no LLM calls made)."""
import pytest
from fastapi.testclient import TestClient

from app import main as M


@pytest.fixture
def client():
    return TestClient(M.app)


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    """Reset rate-limit buckets between tests so order does not matter.

    Must stay a defaultdict - plain dict breaks the bucket auto-creation.
    """
    from collections import defaultdict, deque
    from app import ratelimit
    monkeypatch.setattr(ratelimit, "_buckets", defaultdict(lambda: defaultdict(deque)))


def _project(client):
    r = client.post("/api/projects", json={"name": "t", "prompt": ""})
    assert r.status_code == 200
    return r.json()["id"]


def test_prompt_too_long(client):
    pid = _project(client)
    r = client.post("/api/generate", json={"project_id": pid, "prompt": "x" * 9000})
    assert r.status_code == 422


def test_prompt_empty(client):
    pid = _project(client)
    r = client.post("/api/generate", json={"project_id": pid, "prompt": ""})
    assert r.status_code == 422


def test_instruction_too_long(client):
    pid = _project(client)
    r = client.post("/api/edit", json={"project_id": pid, "instruction": "x" * 5000})
    assert r.status_code == 422


def test_negative_version_rejected(client):
    pid = _project(client)
    r = client.post("/api/rollback", json={"project_id": pid, "version_no": -1})
    assert r.status_code == 422


def test_project_name_too_long(client):
    r = client.post("/api/projects", json={"name": "x" * 200})
    assert r.status_code == 422


def test_rate_limit_returns_429(client, monkeypatch):
    """Schema-invalid requests are rejected (422) before the limiter and consume no quota.

    Use /api/edit with no HTML: the limiter runs first, then it 400s on the missing
    document — cheap, no model call, but still consumes a quota slot.
    """
    monkeypatch.setattr(M.config, "RL_GENERATE", 2)
    codes = [
        client.post("/api/edit", json={"project_id": "rlprobe", "instruction": "改个标题"}).status_code
        for _ in range(4)
    ]
    assert codes[:2] == [400, 400], codes
    assert codes[2] == 429 and codes[3] == 429, codes


def test_missing_project_is_healed_not_404(client):
    """Acting on a project the server lost must self-heal, never 404.

    With no HTML supplied there is nothing to rebuild from, so it degrades to 400
    (a clear business error) rather than a confusing 404.
    """
    r = client.post("/api/edit", json={"project_id": "nope", "instruction": "x"})
    assert r.status_code == 400

    r2 = client.get("/api/projects/nope/versions")
    assert r2.status_code == 200 and r2.json() == []
