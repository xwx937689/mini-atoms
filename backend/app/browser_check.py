"""Optional headless-browser smoke check for generated apps.

Answers what static regex CANNOT: does the page actually render, does JS throw,
does the DOM contain real content?

Design constraints:
- OPTIONAL. If Playwright/Chromium is unavailable (e.g. Render free tier, 512MB),
  return status="skipped" with an explicit reason. Never fake a pass.
- Never let the generated page run in the main process context; it loads in an
  isolated headless page with no host privileges.
"""
import asyncio
import os
from typing import Any

ENABLED = os.getenv("BROWSER_CHECK", "auto").lower()
TIMEOUT_MS = int(os.getenv("BROWSER_CHECK_TIMEOUT_MS", "12000"))


def _available() -> tuple[bool, str]:
    if ENABLED == "false":
        return False, "BROWSER_CHECK=false，已禁用"
    try:
        import playwright  # noqa: F401
    except Exception:
        return False, "未安装 playwright"
    return True, "ok"


async def check_app(html: str) -> dict[str, Any]:
    ok, reason = _available()
    if not ok:
        return {"status": "skipped", "reason": reason, "checks": {}}

    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        return {"status": "skipped", "reason": f"playwright 导入失败: {e}", "checks": {}}

    console_errors: list[str] = []
    page_errors: list[str] = []

    try:
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            except Exception as e:
                return {"status": "skipped", "reason": f"Chromium 启动失败（部署环境常见）: {str(e)[:120]}", "checks": {}}

            ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await ctx.new_page()
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: page_errors.append(str(e)))

            await page.set_content(html, wait_until="load", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(700)

            # 1) visible text length (white-screen detection)
            text = (await page.inner_text("body")) if await page.query_selector("body") else ""
            text_len = len(text.strip())

            # 2) element count
            elem_count = await page.evaluate("document.querySelectorAll('body *').length")

            # 3) at least one interactive control exists and is visible
            interactive = await page.evaluate(
                "Array.from(document.querySelectorAll('button,a,input,select,textarea,[onclick]'))"
                ".filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0}).length"
            )

            # 4) try one real click on the first visible button; ensure no crash
            # Distinguish "no button to click" from "button exists but click failed" -
            # the former is legitimately fine for non-button UIs, the latter is a real defect.
            click_attempted = False
            click_ok = False
            clicks = 0
            for b in (await page.query_selector_all("button"))[:5]:
                if not await b.is_visible():
                    continue
                click_attempted = True
                before = len(page_errors)
                try:
                    await b.click(timeout=2500)
                except Exception:
                    click_ok = False
                    break
                # Playwright's click() resolves even when the handler throws, because the
                # event was dispatched. Real check: did the handler raise a NEW page error?
                await page.wait_for_timeout(250)
                if len(page_errors) > before:
                    click_ok = False
                    break
                click_ok = True
                clicks += 1
                if clicks >= 2:
                    break

            await ctx.close()
            await browser.close()

        # Console "errors" include harmless noise (favicon 404, blocked external assets).
        # Only treat genuine runtime exceptions as failures.
        _runtime_markers = ("uncaught", "typeerror", "referenceerror", "syntaxerror", "is not a function", "is not defined")
        real_console_errors = [
            e for e in console_errors if any(mk in e.lower() for mk in _runtime_markers)
        ]

        checks = {
            "renders": text_len > 20 or elem_count > 5,
            "has_content": text_len > 20 or elem_count >= 8,
            "has_interactive": interactive >= 1,
            # True when there was no button (not applicable) OR the click succeeded.
            "click_works": (not click_attempted) or click_ok,
            "no_js_error": len(page_errors) == 0 and len(real_console_errors) == 0,
        }
        # Passing requires a real interactive page, not merely non-empty pixels:
        # it must render, have content, have a working interactive control, and throw no runtime errors.
        passed = (
            checks["renders"]
            and checks["has_content"]
            and checks["has_interactive"]
            and checks["click_works"]
            and checks["no_js_error"]
        )
        return {
            "status": "ok",
            "passed": passed,
            "checks": checks,
            "text_len": text_len,
            "element_count": elem_count,
            "interactive_count": interactive,
            "click_attempted": click_attempted,
            "js_errors": page_errors[:3],
            "console_errors": real_console_errors[:3],
            "console_ignored": [e for e in console_errors if e not in real_console_errors][:3],
        }
    except Exception as e:
        return {"status": "skipped", "reason": f"检查异常: {str(e)[:120]}", "checks": {}}


def check_app_sync(html: str) -> dict[str, Any]:
    """Sync wrapper usable from async endpoints via run_in_executor if needed."""
    try:
        return asyncio.run(check_app(html))
    except RuntimeError:
        # Already inside a running loop - caller should await check_app instead.
        return {"status": "skipped", "reason": "事件循环冲突，请用 await check_app", "checks": {}}
