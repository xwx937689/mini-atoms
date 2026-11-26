"""Shared fixtures. All tests use a temp SQLite DB and mock the LLM - no network needed."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make `app` importable when pytest runs from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    """Point the DB at a throwaway file so tests never touch real data."""
    from app import config, database as db

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def good_html():
    """A realistic-size, structurally complete single-file app."""
    body = "".join(
        f'<div class="row"><span class="label">指标 {i}</span><b class="val">{i * 7}</b></div>'
        for i in range(30)
    )
    data = ", ".join(f'{{id:{i},name:"项目{i}",status:"ok"}}' for i in range(12))
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>测试看板</title>
<style>
body{{font-family:-apple-system,sans-serif;margin:0;padding:24px;background:#f7f8fa;color:#111}}
.card{{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.row{{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #eee}}
.label{{color:#666}}.val{{color:#4f46e5}}
</style></head><body>
<h1>测试看板</h1>
<div class="card" id="list">{body}</div>
<button id="btn" onclick="document.getElementById('out').textContent='clicked'">刷新数据</button>
<div id="out"></div>
<script>
var rows = [{data}];
function render(){{document.getElementById('out').textContent = '共 ' + rows.length + ' 条记录';}}
document.getElementById('btn').addEventListener('click', render);
render();
</script></body></html>"""
