"""Browser smoke check: white screen / runtime error / click semantics.

These run real Chromium when available; skipped (not failed) otherwise.
"""
import pytest

from app import browser_check

_available = browser_check._available()[0]

pytestmark = pytest.mark.skipif(
    not _available, reason="Playwright/Chromium 不可用（线上免费层常见），跳过而非伪造通过"
)


GOOD = """<!DOCTYPE html><html><head><style>
body{font-family:sans-serif;padding:20px}h1{color:#333}button{padding:8px 16px}
</style></head><body>
<h1>测试应用</h1><p>这是一个用于验证浏览器检查的页面，包含足够的正文内容来通过内容量判定。</p>
<button id="b" onclick="document.getElementById('o').textContent='已点击'">点击我</button>
<div id="o">等待操作</div>
<script>console.error('favicon 404: harmless noise');</script>
</body></html>"""

WHITE_SCREEN = """<!DOCTYPE html><html><head><style>body{background:#fff}</style></head><body>
<div id="root"></div><script>throw new Error("fatal boom");</script></body></html>"""

CLICK_BROKEN = """<!DOCTYPE html><html><head><style>body{padding:20px}</style></head><body>
<h1>标题</h1><p>这段正文足够长，可以满足内容量要求，用于单独验证点击失败是否影响判定结果。</p>
<button id="b" onclick="throw new Error('handler exploded')">会爆炸的按钮</button>
</body></html>"""


@pytest.mark.asyncio
async def test_good_page_passes():
    r = await browser_check.check_app(GOOD)
    assert r["status"] == "ok"
    assert r["passed"] is True
    assert r["checks"]["has_interactive"] is True
    # console.error noise must NOT be counted as a runtime failure
    assert r["checks"]["no_js_error"] is True


@pytest.mark.asyncio
async def test_white_screen_and_js_error_fails():
    r = await browser_check.check_app(WHITE_SCREEN)
    assert r["status"] == "ok"
    assert r["passed"] is False
    assert r["checks"]["no_js_error"] is False


@pytest.mark.asyncio
async def test_click_failure_is_detected():
    """A visible button whose handler throws must not be counted as a working interaction."""
    r = await browser_check.check_app(CLICK_BROKEN)
    assert r["status"] == "ok"
    assert r["click_attempted"] is True
    assert r["checks"]["click_works"] is False
    assert r["passed"] is False


@pytest.mark.asyncio
async def test_no_button_does_not_penalise_click():
    """Non-button UIs (inputs only) should not be failed for having nothing to click."""
    html = """<!DOCTYPE html><html><head><style>body{padding:20px}</style></head><body>
    <h1>表单页面</h1><p>这是一个只有输入框没有按钮的页面，用于验证点击判定不会误伤这类界面。</p>
    <input type="text" placeholder="输入内容"><select><option>选项A</option></select>
    </body></html>"""
    r = await browser_check.check_app(html)
    assert r["status"] == "ok"
    assert r["click_attempted"] is False
    assert r["checks"]["click_works"] is True
