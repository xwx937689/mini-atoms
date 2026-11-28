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


@pytest.mark.asyncio
async def test_build_retry_actually_cuts_scope(monkeypatch, good_html):
    """Saying "be shorter" is not enough - the retry must drop features from the plan.

    Otherwise the model re-plans the same oversized app and truncates again.
    """
    client = LLMClient()
    client.ready = True
    seen = []
    calls = {"n": 0}

    async def fake_complete(system, user, **kw):
        seen.append(user)
        calls["n"] += 1
        return '<html><body><script>x=' if calls["n"] == 1 else good_html
    monkeypatch.setattr(client, "complete", fake_complete)

    plan = {
        "app_name": "电商", "summary": "s",
        "features": ["登录", "商品展示", "购物车", "下单支付", "订单管理", "优惠券", "评价"],
        "pages": ["首页", "购物车", "订单", "个人中心"],
        "components": ["nav", "card", "modal", "table", "chart", "footer"],
        "layout": "", "style": "", "data": [], "acceptance": [],
    }
    await client.build("做个电商系统", plan)

    retry_prompt = seen[1]
    # 只保留前 4 项，其余裁掉
    assert "登录" in retry_prompt
    assert "商品展示" in retry_prompt
    assert "购物车" in retry_prompt
    assert "下单支付" in retry_prompt
    assert "订单管理" not in retry_prompt, "第 5 项起应被裁掉"
    assert "优惠券" not in retry_prompt
    assert "评价" not in retry_prompt
    # pages / components 也要收敛
    assert "个人中心" not in retry_prompt
    # 预算必须写死在提示里，而不是只说"短一点"
    assert "字符" in retry_prompt
