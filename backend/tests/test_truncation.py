"""Truncation detection and refusal. A cut-off document must never be saved
just because clean_html can close its tags."""
import pytest

from app.llm import LLMClient, LLMError, looks_truncated


def test_detects_missing_html_close():
    assert looks_truncated("<html><body><p>hi</p>") is True


def test_detects_unbalanced_script():
    raw = '<html><body><script>const a = 1;'
    assert looks_truncated(raw) is True


def test_detects_unbalanced_braces():
    raw = '<!DOCTYPE html><html><head><style>x{}</style></head><body><script>function f(){ if(1){ </script></body></html>'
    assert looks_truncated(raw) is True


def test_complete_document_is_fine(good_html):
    assert looks_truncated(good_html) is False


@pytest.mark.asyncio
async def test_build_raises_when_truncated_twice(monkeypatch, good_html):
    """Even if clean_html could close the tags, a twice-truncated output must be rejected."""
    client = LLMClient()
    client.ready = True
    truncated = '<!DOCTYPE html><html><body><script>const x = {a:1;'

    async def fake_complete(*a, **k):
        return truncated
    monkeypatch.setattr(client, "complete", fake_complete)

    with pytest.raises(LLMError, match="截断"):
        await client.build("生成一个应用", {"app_name": "x", "features": []})


@pytest.mark.asyncio
async def test_edit_raises_when_still_truncated(monkeypatch):
    client = LLMClient()
    client.ready = True
    truncated = '<!DOCTYPE html><html><body><script>function g(){ '

    async def fake_complete(*a, **k):
        return truncated
    monkeypatch.setattr(client, "complete", fake_complete)

    with pytest.raises(LLMError, match="截断"):
        await client.edit("原始需求", "<html></html>", [], "加个搜索框")


@pytest.mark.asyncio
async def test_build_succeeds_on_retry(monkeypatch, good_html):
    """First attempt truncated, second complete -> should return the good html."""
    client = LLMClient()
    client.ready = True
    calls = {"n": 0}

    async def fake_complete(*a, **k):
        calls["n"] += 1
        return '<html><body><script>x=' if calls["n"] == 1 else good_html
    monkeypatch.setattr(client, "complete", fake_complete)

    html = await client.build("生成一个看板", {"app_name": "x", "features": []})
    assert "</html>" in html
    assert calls["n"] == 2
