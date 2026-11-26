"""Quality gate behaviour. LLM is mocked so results are deterministic.

Covers: review pass / fail / error, browser fail / skipped, full re-verification
after repair, and the hard guarantee that a failed gate never writes a version.
"""
import pytest

from app import main as M
from app.llm import LLMError


def _review(valid=True, status="ok", issues=None, score=90, mode="llm"):
    async def fn(_html):
        return {"status": status, "valid": valid, "issues": issues or [],
                "score": score, "mode": mode, **({"error": "boom"} if status == "error" else {})}
    return fn


def _browser(passed=True, status="ok"):
    async def fn(_html):
        return {"status": status, "passed": passed,
                "checks": {"renders": passed, "has_content": passed,
                           "has_interactive": passed, "click_works": passed,
                           "no_js_error": passed},
                "js_errors": [] if passed else ["boom"],
                "element_count": 80 if passed else 2,
                "interactive_count": 5 if passed else 0,
                "reason": "" if status == "ok" else "Chromium 不可用"}
    return fn


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Default: real-AI mode, browser enabled, no repair side effects."""
    monkeypatch.setattr(M.llm, "ready", True)
    monkeypatch.setattr(M.browser_check, "ENABLED", "true")

    async def noop_repair(html, issues):
        return html
    monkeypatch.setattr(M.llm, "repair", noop_repair)


@pytest.mark.asyncio
async def test_review_pass_allows_save(good_html, monkeypatch):
    monkeypatch.setattr(M.llm, "review", _review(valid=True))
    monkeypatch.setattr(M.browser_check, "check_app", _browser(passed=True))
    _, gate = await M.quality_gate(good_html, [], allow_repair=False)
    assert gate["final_status"] == "passed"
    assert gate["verified"] is True


@pytest.mark.asyncio
async def test_review_fail_blocks(good_html, monkeypatch):
    monkeypatch.setattr(M.llm, "review", _review(valid=False, issues=["缺少筛选功能"]))
    monkeypatch.setattr(M.browser_check, "check_app", _browser(passed=True))
    with pytest.raises(LLMError):
        await M.quality_gate(good_html, [], allow_repair=False)


@pytest.mark.asyncio
async def test_review_error_blocks_never_passes(good_html, monkeypatch):
    """The core P0: a reviewer that errors must NOT be treated as approval."""
    monkeypatch.setattr(M.llm, "review", _review(status="error"))
    monkeypatch.setattr(M.browser_check, "check_app", _browser(passed=True))
    with pytest.raises(LLMError, match="审查异常|未通过"):
        await M.quality_gate(good_html, [], allow_repair=False)


@pytest.mark.asyncio
async def test_browser_fail_blocks_and_never_logs_pass(good_html, monkeypatch):
    monkeypatch.setattr(M.llm, "review", _review(valid=True))
    monkeypatch.setattr(M.browser_check, "check_app", _browser(passed=False))
    steps = []
    with pytest.raises(LLMError):
        await M.quality_gate(good_html, steps, allow_repair=True)
    assert not any(s["action"] == "质量校验通过" for s in steps)


@pytest.mark.asyncio
async def test_repair_triggers_full_reverify(good_html, monkeypatch):
    """After a repair the gate must re-run review AND browser, not just static check."""
    monkeypatch.setattr(M.llm, "review", _review(valid=True))
    calls = {"n": 0}

    async def flaky(_html):
        calls["n"] += 1
        return await _browser(passed=(calls["n"] > 1))(_html)
    monkeypatch.setattr(M.browser_check, "check_app", flaky)

    _, gate = await M.quality_gate(good_html, [], allow_repair=True)
    assert gate["final_status"] == "passed"
    assert gate["rounds"] >= 2
    assert calls["n"] >= 2, "browser check must run again after repair"


@pytest.mark.asyncio
async def test_skipped_is_not_passed(good_html, monkeypatch):
    monkeypatch.setattr(M.llm, "review", _review(valid=True))
    monkeypatch.setattr(M.browser_check, "check_app", _browser(status="skipped"))
    steps = []
    _, gate = await M.quality_gate(good_html, steps, allow_repair=False)
    assert gate["final_status"] == "passed_with_skips"
    assert not any("全部" in s["detail"] for s in steps if s["action"].startswith("质量校验"))
    assert any("部分检查未执行" in s["action"] for s in steps)


@pytest.mark.asyncio
async def test_mock_mode_never_claims_model_verified(good_html, monkeypatch):
    monkeypatch.setattr(M.llm, "ready", False)
    monkeypatch.setattr(M.browser_check, "check_app", _browser(passed=True))
    _, gate = await M.quality_gate(good_html, [], allow_repair=False)
    assert gate["verified"] is False
    assert gate["review_mode"] == "mock"


@pytest.mark.asyncio
async def test_failed_gate_does_not_write_version(good_html, monkeypatch):
    """A rejected gate must leave current_html and version list untouched."""
    from app import database as db

    pid = "gatefail01"
    db.create_project(pid, "t")
    original = "<html>previous</html>"
    db.update_project_html(pid, original)

    monkeypatch.setattr(M.llm, "review", _review(status="error"))
    monkeypatch.setattr(M.browser_check, "check_app", _browser(passed=True))
    with pytest.raises(LLMError):
        await M.quality_gate(good_html, [], allow_repair=False)

    assert db.get_project(pid)["current_html"] == original
    assert len(db.list_versions(pid)) == 0
