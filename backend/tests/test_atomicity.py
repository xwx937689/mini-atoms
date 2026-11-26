"""Atomicity + API-level failure tests.

1) save_project_version must be all-or-nothing.
2) /api/generate and /api/edit must leave current_html and versions untouched
   when the quality gate rejects the result.
"""
import pytest
from fastapi.testclient import TestClient

from app import database as db
from app import main as M
from app.llm import LLMError


# ---------- database atomicity ----------

def test_save_project_version_writes_both():
    pid = "atom0001"
    db.create_project(pid, "t")
    db.save_project_version(pid, 1, "V1: a", "<html>one</html>", plan={"app_name": "a"})

    assert db.get_project(pid)["current_html"] == "<html>one</html>"
    assert [v["version_no"] for v in db.list_versions(pid)] == [1]


def test_transaction_rolls_back_mid_write():
    """Any exception inside a get_conn block must discard ALL writes in it."""
    pid = "atom0002"
    db.create_project(pid, "t")
    db.update_project_html(pid, "<html>original</html>")

    with pytest.raises(RuntimeError):
        with db.get_conn() as conn:
            conn.execute("UPDATE projects SET current_html=? WHERE id=?", ("<html>changed</html>", pid))
            conn.execute(
                "INSERT INTO versions(project_id,version_no,label,html,plan,created_at) "
                "VALUES(?,?,?,?,?,?)", (pid, 1, "V1", "<html>changed</html>", "{}", "now"),
            )
            raise RuntimeError("boom mid-transaction")

    assert db.get_project(pid)["current_html"] == "<html>original</html>"
    assert db.list_versions(pid) == []


def test_save_project_version_is_atomic_pair():
    """Both rows change together - the invariant callers rely on."""
    pid = "atom0003"
    db.create_project(pid, "t")
    before_html = db.get_project(pid)["current_html"]
    db.save_project_version(pid, 1, "V1", "<html>v1</html>")
    after = db.get_project(pid)
    versions = db.list_versions(pid)
    assert after["current_html"] != before_html
    assert len(versions) == 1 and versions[0]["version_no"] == 1


# ---------- API-level failure paths ----------

@pytest.fixture
def client():
    return TestClient(M.app)


def _seed_project(client, html="<html>previous</html>"):
    pid = client.post("/api/projects", json={"name": "t", "prompt": ""}).json()["id"]
    db.update_project_html(pid, html)
    return pid


@pytest.fixture
def failing_gate(monkeypatch):
    """Make the model review always error -> gate must reject."""
    async def err_review(_html):
        return {"status": "error", "valid": False, "issues": [], "score": 0,
                "error": "simulated timeout", "mode": "llm"}

    async def passthrough_build(prompt, plan):
        return ('<!DOCTYPE html><html><head><style>body{font:14px sans-serif}'
                'h1{color:#111}</style></head><body><h1>新应用</h1>'
                '<button onclick="this.textContent=1">b</button>'
                '<script>var x=1;</script></body></html>')

    async def passthrough_edit(op, cur, hist, ins):
        return ('<!DOCTYPE html><html><head><style>body{font:14px sans-serif}'
                'h1{color:#111}</style></head><body><h1>改过</h1>'
                '<button onclick="this.textContent=2">b</button>'
                '<script>var y=2;</script></body></html>')

    monkeypatch.setattr(M.llm, "ready", True)
    monkeypatch.setattr(M.llm, "review", err_review)
    monkeypatch.setattr(M.llm, "build", passthrough_build)
    monkeypatch.setattr(M.llm, "edit", passthrough_edit)
    monkeypatch.setattr(M.browser_check, "ENABLED", "false")

    async def fake_plan(*a, **k):
        return {"app_name": "x", "features": ["a"], "pages": [], "components": [],
                "data": [], "acceptance": [], "layout": "", "style": "", "summary": ""}
    monkeypatch.setattr(M.llm, "plan", fake_plan)

    async def no_repair(html, issues):
        return html
    monkeypatch.setattr(M.llm, "repair", no_repair)


def test_generate_failure_keeps_previous_state(client, failing_gate):
    pid = _seed_project(client, "<html>previous</html>")
    before_html = db.get_project(pid)["current_html"]
    before_versions = len(db.list_versions(pid))

    r = client.post("/api/generate", json={"project_id": pid, "prompt": "生成一个新应用"})
    assert r.status_code == 500
    assert r.json()["ok"] is False

    assert db.get_project(pid)["current_html"] == before_html
    assert len(db.list_versions(pid)) == before_versions


def test_edit_failure_keeps_previous_state(client, failing_gate):
    prev = "<html>" + ("x" * 2000) + "</html>"
    pid = _seed_project(client, prev)
    db.save_project_version(pid, 1, "V1", prev)   # existing version
    before_versions = len(db.list_versions(pid))

    r = client.post("/api/edit", json={"project_id": pid, "instruction": "改一下标题"})
    assert r.status_code == 500
    assert r.json()["ok"] is False

    assert db.get_project(pid)["current_html"] == prev
    assert len(db.list_versions(pid)) == before_versions


def test_successful_generate_creates_exactly_one_version(client, monkeypatch):
    """Baseline: the happy path still writes html + exactly one version, atomically."""
    pid = _seed_project(client, "")

    async def ok_plan(*a, **k):
        return {"app_name": "good", "features": ["f"], "pages": [], "components": [],
                "data": [], "acceptance": [], "layout": "", "style": "", "summary": ""}

    async def ok_build(prompt, plan):
        body = "".join(f"<div class='r'>{i}</div>" for i in range(40))
        return (f'<!DOCTYPE html><html><head><style>body{{font:14px sans-serif;padding:10px}}'
                f'.r{{padding:4px;border-bottom:1px solid #eee}}</style></head><body>'
                f'<h1>看板</h1>{body}<button onclick="this.textContent=1">刷新</button>'
                f'<script>var d=[1,2,3];</script></body></html>')

    async def ok_review(_html):
        return {"status": "ok", "valid": True, "issues": [], "score": 95, "mode": "llm"}

    monkeypatch.setattr(M.llm, "ready", True)
    monkeypatch.setattr(M.llm, "plan", ok_plan)
    monkeypatch.setattr(M.llm, "build", ok_build)
    monkeypatch.setattr(M.llm, "review", ok_review)
    monkeypatch.setattr(M.browser_check, "ENABLED", "false")

    r = client.post("/api/generate", json={"project_id": pid, "prompt": "生成一个看板"})
    assert r.status_code == 200, r.text
    assert len(db.list_versions(pid)) == 1
    assert db.get_project(pid)["current_html"] == r.json()["html"]
