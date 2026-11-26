"""Self-heal: the free tier wipes SQLite on restart while the client keeps a mirror.

Acting on a project the server no longer knows about must NOT fail with 404 —
it should rebuild the row from the HTML the client sends.
"""
import pytest
from fastapi.testclient import TestClient

from app import database as db
from app import main as M


@pytest.fixture
def client():
    return TestClient(M.app)


@pytest.fixture(autouse=True)
def no_limits(monkeypatch):
    monkeypatch.setattr(M.config, "RL_GENERATE", 1000)
    monkeypatch.setattr(M.browser_check, "ENABLED", "false")


BODY = ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<title>t</title><style>body{font-family:sans-serif;padding:24px}'
        '.row{padding:6px 0;border-bottom:1px solid #eee}</style></head><body>'
        '<h1>看板</h1>' + "".join(f'<div class="row">指标 {i}</div>' for i in range(30)) +
        '<button onclick="this.textContent=1">刷新</button>'
        '<script>var d=[1,2,3];</script></body></html>')


def test_publish_heals_missing_project(client):
    """The exact bug reported: publish 404 after a free-tier restart."""
    assert db.get_project("ghost001") is None

    r = client.post("/api/publish", json={"project_id": "ghost001", "title": "健身管家", "html": BODY})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # project row rebuilt, and the published page is reachable
    assert db.get_project("ghost001") is not None
    slug = r.json()["slug"]
    assert client.get(f"/p/{slug}").status_code == 200


def test_publish_without_html_still_rejects(client):
    r = client.post("/api/publish", json={"project_id": "ghost002"})
    assert r.status_code == 400


def test_edit_heals_missing_project(client, monkeypatch):
    async def fake_edit(op, cur, hist, ins):
        return cur.replace("<h1>看板</h1>", "<h1>看板 v2</h1>")
    monkeypatch.setattr(M.llm, "ready", True)
    monkeypatch.setattr(M.llm, "edit", fake_edit)
    monkeypatch.setattr(M.llm, "review", lambda *_: None)

    async def ok_review(_h):
        return {"status": "ok", "valid": True, "issues": [], "score": 92, "mode": "llm"}
    monkeypatch.setattr(M.llm, "review", ok_review)

    r = client.post("/api/edit", json={"project_id": "ghost003", "instruction": "改标题", "current_html": BODY})
    assert r.status_code == 200, r.text
    assert "看板 v2" in r.json()["html"]
    assert db.get_project("ghost003") is not None


def test_generate_heals_missing_project(client, monkeypatch):
    async def fake_plan(*a, **k):
        return {"app_name": "x", "features": [], "pages": [], "components": [],
                "data": [], "acceptance": [], "layout": "", "style": "", "summary": ""}

    async def fake_build(*a, **k):
        return BODY

    async def ok_review(_h):
        return {"status": "ok", "valid": True, "issues": [], "score": 92, "mode": "llm"}
    monkeypatch.setattr(M.llm, "ready", True)
    monkeypatch.setattr(M.llm, "plan", fake_plan)
    monkeypatch.setattr(M.llm, "build", fake_build)
    monkeypatch.setattr(M.llm, "review", ok_review)
    monkeypatch.setattr(M.llm, "repair", lambda h, i: _async(h))
    monkeypatch.setattr(M.browser_check, "check_app", _reject_browser)

    async def patched_gate(html, steps, allow_repair=True):
        return html, {"final_status": "passed", "verified": True, "review_status": "ok",
                      "review_mode": "llm", "review_score": 92, "static_issues": [],
                      "browser": {"status": "skipped"}, "rounds": 1}
    monkeypatch.setattr(M, "quality_gate", patched_gate)

    r = client.post("/api/generate", json={"project_id": "ghost004", "prompt": "生成一个看板"})
    assert r.status_code == 200, r.text
    assert db.get_project("ghost004") is not None


def test_versions_returns_empty_for_missing_project(client):
    r = client.get("/api/projects/ghost005/versions")
    assert r.status_code == 200
    assert r.json() == []


def test_rollback_gives_friendly_message(client):
    r = client.post("/api/rollback", json={"project_id": "ghost006", "version_no": 1})
    assert r.status_code == 409
    assert "重置" in r.json()["detail"]


async def _async(v):
    return v


async def _reject_browser(_h):
    return {"status": "skipped", "reason": "test", "checks": {}}
