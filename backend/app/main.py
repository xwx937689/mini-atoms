"""Mini Atoms API - AI-driven web app generation workbench."""
import asyncio
import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import browser_check, config, database as db, mock, ratelimit
from .llm import LLMClient, LLMError
from .validator import static_check
from .scoring import score_app

app = FastAPI(title="Mini Atoms API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if config.CORS_ALLOW_ALL else config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
llm = LLMClient()
db.init_db()


# ---------------- schemas ----------------

class CreateProjectIn(BaseModel):
    name: str = Field(default="未命名应用", max_length=80)
    prompt: str = Field(default="", max_length=8000)


class GenerateIn(BaseModel):
    project_id: str = Field(..., max_length=64)
    prompt: str = Field(..., min_length=1, max_length=8000)


class EditIn(BaseModel):
    project_id: str = Field(..., max_length=64)
    instruction: str = Field(..., min_length=1, max_length=4000)
    # Client-side mirror of the current document. Used only when the server lost the
    # project (free-tier restart) so editing still works instead of returning 404.
    current_html: Optional[str] = Field(default=None, max_length=2_000_000)


class RollbackIn(BaseModel):
    project_id: str = Field(..., max_length=64)
    version_no: int = Field(..., ge=1)


class PublishIn(BaseModel):
    project_id: str = Field(..., max_length=64)
    title: Optional[str] = Field(default=None, max_length=80)
    # Same rescue path as EditIn: lets publishing survive a wiped server DB.
    html: Optional[str] = Field(default=None, max_length=2_000_000)


class RenameIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


# ---------------- helpers ----------------

def _activity(agent: str, action: str, tool: str = "", detail: str = "") -> dict:
    return {"agent": agent, "action": action, "tool": tool, "detail": detail}


def _ok(project_id: str) -> dict:
    p = db.get_project(project_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


async def _run_checks(html: str) -> dict:
    """Run ALL quality checks once: static structure, model review, browser smoke."""
    static = static_check(html)
    if llm.ready:
        review = await llm.review(html)
        if review["status"] == "error":
            # One retry to smooth over transient model hiccups before we block.
            review = await llm.review(html)
        review["mode"] = "llm"
    else:
        m = mock.review(html)
        review = {"status": "ok", "valid": bool(m.get("valid", True)),
                  "issues": list(m.get("issues") or []), "score": m.get("score"), "mode": "mock"}
    browser = {"status": "skipped", "reason": "未启用"}
    if browser_check.ENABLED != "false":
        browser = await browser_check.check_app(html)
    return {"static": static, "review": review, "browser": browser}


def _judge(checks: dict) -> tuple[bool, list[str]]:
    """Decide pass/fail plus human-readable blockers. Skipped != passed."""
    blockers: list[str] = []
    if not checks["static"]["valid"]:
        blockers.extend(checks["static"]["issues"])
    rv = checks["review"]
    if rv["status"] == "error":
        blockers.append(f"模型审查异常（无法确认为合格）：{rv.get('error', '')[:80]}")
    elif rv["status"] == "ok" and not rv["valid"]:
        blockers.extend(rv.get("issues") or ["模型审查未通过"])
    br = checks["browser"]
    if br["status"] == "ok" and not br.get("passed", False):
        blockers.append("浏览器运行验证未通过")
        blockers.extend(br.get("js_errors") or [])
    return (len(blockers) == 0), blockers


async def quality_gate(html: str, steps: list[dict], allow_repair: bool = True) -> tuple[str, dict]:
    """Strict unified gate for EVERY save path (generate / edit / mock fallback).

    Each round runs the FULL check set. If anything fails, repair once and
    re-run everything - a repair is never trusted without full re-verification.
    If it still fails, raise: no version is written, previous version survives.

    Hard rule: a reviewer error (status=error) BLOCKS saving in real-AI mode.
    It is never silently treated as "passed". Skipped browser check is reported
    as skipped, never as passed.
    """
    max_rounds = 2 if allow_repair else 1
    checks = await _run_checks(html)
    ok, blockers = _judge(checks)

    for round_no in range(max_rounds):
        if ok:
            break
        if round_no == max_rounds - 1:
            break
        steps.append(_activity("Reviewer", "发现问题，请求修复", "Repair", "；".join(blockers[:3])))
        html = await llm.repair(html, blockers)
        checks = await _run_checks(html)
        ok, blockers = _judge(checks)

    rv, br = checks["review"], checks["browser"]
    if rv["status"] == "error":
        steps.append(_activity("Reviewer", "模型审查异常，已阻断保存", "Review", "REVIEW_ERROR"))
    elif rv["status"] == "ok" and not rv["valid"]:
        steps.append(_activity("Reviewer", "模型审查未通过", "Review", "；".join((rv.get("issues") or [])[:3])))
    elif rv.get("mode") == "mock":
        steps.append(_activity("Reviewer", "演示模式校验（非真实模型审查）", "Review", "mock_mode"))
    else:
        steps.append(_activity("Reviewer", "模型审查通过", "Review", f"评分 {rv.get('score') if rv.get('score') is not None else '-'}"))
    if br["status"] == "ok":
        steps.append(_activity("Reviewer", "浏览器运行验证" + ("通过" if br.get("passed") else "未通过"),
                               "Run browser", f"元素 {br.get('element_count', 0)} · 交互 {br.get('interactive_count', 0)} · JS错误 {len(br.get('js_errors') or [])}"))
    else:
        steps.append(_activity("Reviewer", "浏览器验证 SKIPPED（非通过）", "Run browser", br.get("reason", "")))

    if not ok:
        raise LLMError("质量门禁未通过，已保留上一可用版本：" + "；".join(blockers[:3]))

    if br["status"] == "ok":
        steps.append(_activity("Reviewer", "质量校验通过", "Run check", "结构 + 模型审查 + 浏览器验证 全部执行"))
    else:
        steps.append(_activity("Reviewer", "质量校验完成（部分检查未执行）", "Run check",
                               "浏览器验证不可用，已跳过；仅完成结构校验与模型审查"))
    return html, {
        "final_status": "passed" if br["status"] == "ok" else "passed_with_skips",
        "verified": rv["status"] == "ok" and rv.get("mode") != "mock",
        "review_status": rv["status"],
        "review_mode": rv.get("mode"),
        "review_score": rv.get("score"),
        "static_issues": checks["static"]["issues"],
        "browser": br,
        "rounds": round_no + 1,
    }



# ---------------- system ----------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "llm_ready": llm.ready, "model": config.LLM_MODEL}


@app.get("/api/config")
async def get_config():
    return {"llm_ready": llm.ready, "model": config.LLM_MODEL, "mock_mode": not llm.ready}


# ---------------- projects ----------------

@app.post("/api/projects")
async def create_project(body: CreateProjectIn):
    pid = uuid.uuid4().hex[:12]
    return db.create_project(pid, body.name, body.prompt)


@app.get("/api/projects")
async def list_projects():
    return db.list_projects()


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    p = _ok(project_id)
    p["messages"] = db.get_messages(project_id)
    p["versions"] = db.list_versions(project_id)
    if p.get("current_html"):
        plan_msg = next((m for m in p["messages"] if m.get("kind") == "plan"), None)
        plan = (plan_msg.get("extra") or {}).get("plan") if plan_msg else None
        p["quality"] = score_app(p["current_html"], plan)
    return p


@app.patch("/api/projects/{project_id}/rename")
async def rename_project(project_id: str, body: RenameIn):
    _ok(project_id)
    with db.get_conn() as conn:
        conn.execute("UPDATE projects SET name=?,updated_at=? WHERE id=?", (body.name, db._now(), project_id))
    return db.get_project(project_id)


@app.delete("/api/projects/{project_id}")
async def remove_project(project_id: str):
    _ok(project_id)
    db.delete_project(project_id)
    return {"ok": True}


# ---------------- generation ----------------

@app.post("/api/generate")
async def generate(body: GenerateIn, request: Request):
    allowed, retry = ratelimit.check("generate", ratelimit.client_ip(request), config.RL_GENERATE, config.RL_WINDOW)
    if not allowed:
        return JSONResponse(status_code=429, content={
            "ok": False, "error": f"请求过于频繁，请 {retry} 秒后再试（每 {config.RL_WINDOW // 60} 分钟最多 {config.RL_GENERATE} 次）"
        })
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(400, "需求不能为空")

    # Self-heal: the client may still show a project whose server row was lost on restart.
    p = db.get_project(body.project_id)
    if not p:
        db.ensure_project(body.project_id, "未命名应用", "", prompt)
        p = db.get_project(body.project_id)

    steps: list[dict] = []

    db.add_message(body.project_id, "user", prompt, kind="prompt")
    steps.append(_activity("Mike", "接收到构建任务", "Read request", prompt[:80]))

    try:
        if llm.ready:
            plan = await llm.plan(prompt, [])
        else:
            await asyncio.sleep(0.25)
            plan = mock.plan(prompt)
        steps.append(_activity("Mike", "完成需求拆解，形成应用规划", "Write brief", json.dumps(plan, ensure_ascii=False)[:400]))
        db.add_message(body.project_id, "agent", json.dumps(plan, ensure_ascii=False), agent="Mike · Team Leader", kind="plan", extra={"plan": plan, "steps": steps[:]})

        steps.append(_activity("Alex", "正在生成完整 HTML 应用", "Write code", f"规划 {len(plan.get('features', []))} 项交互功能"))
        html = await llm.build(prompt, plan) if llm.ready else mock.build(prompt, plan)

        # unified quality gate: static check -> model review -> one repair -> re-check
        html, gate = await quality_gate(html, steps, allow_repair=llm.ready)

        quality = score_app(html, plan)

        version_no = db.next_version_no(body.project_id)
        name = plan.get("app_name") or p["name"]
        db.save_project_version(body.project_id, version_no, f"V{version_no}: {name}", html,
                                plan=plan, prompt=prompt, name=name)
        db.add_message(
            body.project_id, "agent", f"应用已生成并通过校验。", agent="Alex · Engineer", kind="result",
            extra={"steps": steps, "version_no": version_no},
        )
        return {
            "ok": True,
            "project_id": body.project_id,
            "version_no": version_no,
            "plan": plan,
            "html": html,
            "steps": steps,
            "quality": quality,
            "gate": gate,
            "mock_mode": not llm.ready,
        }
    except Exception as e:
        # Only fall back when the LLM transport itself failed (dead key / quota / network).
        # NEVER replace a successfully generated-but-imperfect result with generic mock data.
        if llm.ready and isinstance(e, LLMError) and "模型请求失败" in str(e):
            try:
                plan = plan if 'plan' in locals() else mock.plan(prompt)
                html = mock.build(prompt, plan)
                steps.append(_activity("System", "模型服务不可用，切换到演示模式生成", "Fallback", str(e)[:80]))
                # Mock output must pass the SAME gate - no bypass on the fallback path.
                html, gate = await quality_gate(html, steps, allow_repair=False)
                version_no = db.next_version_no(body.project_id)
                name = plan.get("app_name") or p["name"]
                db.save_project_version(body.project_id, version_no, f"V{version_no}: {name}", html,
                                        plan=plan, prompt=prompt, name=name)
                db.add_message(body.project_id, "agent", "模型服务不可用，已切换到演示模式生成。", agent="System", kind="result", extra={"version_no": version_no, "steps": steps})
                return {
                    "ok": True, "project_id": body.project_id, "version_no": version_no,
                    "plan": plan, "html": html, "steps": steps, "mock_mode": True,
                    "quality": score_app(html, plan), "gate": gate,
                    "fallback": True, "fallback_reason": str(e)[:200],
                }
            except Exception as mock_err:
                e = Exception(f"{e}；演示模式也失败：{mock_err}")
        db.add_message(body.project_id, "agent", f"生成失败：{e}", agent="System", kind="error")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e), "steps": steps})


@app.post("/api/edit")
async def edit_app(body: EditIn, request: Request):
    allowed, retry = ratelimit.check("edit", ratelimit.client_ip(request), config.RL_GENERATE, config.RL_WINDOW)
    if not allowed:
        return JSONResponse(status_code=429, content={
            "ok": False, "error": f"请求过于频繁，请 {retry} 秒后再试（每 {config.RL_WINDOW // 60} 分钟最多 {config.RL_GENERATE} 次）"
        })
    instruction = body.instruction.strip()
    if not instruction:
        raise HTTPException(400, "修改要求不能为空")

    # Self-heal: the server may have restarted and lost this project while the client
    # still holds a mirror of it. Rebuild from the HTML the client sends, so editing
    # keeps working instead of failing with "project not found".
    p = db.get_project(body.project_id)
    base_html = (p["current_html"] if p else "") or (body.current_html or "")
    if not base_html:
        raise HTTPException(400, "请先生成应用")
    if not p or not (p["current_html"] or ""):
        db.ensure_project(body.project_id, "未命名应用", base_html)
        p = db.get_project(body.project_id)

    db.add_message(body.project_id, "user", instruction, kind="edit")
    history = db.get_messages(body.project_id)
    steps = [_activity("Mike", "理解修改要求，检查当前版本", "Read context", instruction[:80])]

    try:
        steps.append(_activity("Alex", "在现有版本上应用修改", "Edit code", "保留已有功能"))
        html = await llm.edit(p["original_prompt"], base_html, history, instruction) if llm.ready else mock.edit(p["original_prompt"], base_html, history, instruction)

        # Same unified gate as generate. On failure we raise BEFORE touching the DB,
        # so the previous good version is preserved.
        html, gate = await quality_gate(html, steps, allow_repair=llm.ready)

        # Regression guard: a "successful" edit that returns a drastically smaller
        # document has almost certainly dropped prior features. Reject it instead of
        # silently replacing a working version with a hollow shell.
        prev_len = len(base_html)
        if prev_len >= 1500 and len(html) < prev_len * 0.65:
            raise LLMError(
                f"修改后内容明显缩水（{prev_len} → {len(html)} 字符），疑似丢失既有功能，已保留上一可用版本"
            )

        quality = score_app(html, None)

        version_no = db.next_version_no(body.project_id)
        db.save_project_version(body.project_id, version_no, f"V{version_no}: {instruction[:24]}", html)
        db.add_message(body.project_id, "agent", "修改已完成并生成新版本。", agent="Alex · Engineer", kind="result", extra={"steps": steps, "version_no": version_no})
        return {"ok": True, "version_no": version_no, "html": html, "steps": steps, "quality": quality, "gate": gate, "mock_mode": not llm.ready}
    except Exception as e:
        # Only fall back on transport failure. A validation failure after a successful model
        # call must NOT wipe the previous good version with generic mock output.
        if llm.ready and isinstance(e, LLMError) and "模型请求失败" in str(e):
            try:
                html = mock.edit(p["original_prompt"], p["current_html"], history, instruction)
                steps.append(_activity("System", "模型服务不可用，切换到演示模式修改", "Fallback", str(e)[:80]))
                # Same gate as the normal path - fallback must not bypass verification.
                html, gate = await quality_gate(html, steps, allow_repair=False)
                prev_len = len(p["current_html"] or "")
                if prev_len >= 1500 and len(html) < prev_len * 0.65:
                    raise LLMError(f"演示模式修改后内容缩水（{prev_len} → {len(html)}），已保留上一可用版本")
                version_no = db.next_version_no(body.project_id)
                db.save_project_version(body.project_id, version_no, f"V{version_no}: {instruction[:24]}", html)
                db.add_message(body.project_id, "agent", "模型服务不可用，已用演示模式应用修改。", agent="System", kind="result", extra={"version_no": version_no, "steps": steps})
                return {"ok": True, "version_no": version_no, "html": html, "steps": steps,
                        "quality": score_app(html, None), "gate": gate, "mock_mode": True, "fallback": True}
            except Exception as mock_err:
                e = Exception(f"{e}；演示模式也失败：{mock_err}")
        db.add_message(body.project_id, "agent", f"修改失败，已保留上一可用版本：{e}", agent="System", kind="error")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e), "steps": steps})


# ---------------- versions ----------------

@app.get("/api/projects/{project_id}/versions")
async def versions(project_id: str):
    # Empty list (not 404) when the server lost the row: the client still renders fine.
    return db.list_versions(project_id)


@app.post("/api/rollback")
async def rollback(body: RollbackIn):
    p = db.get_project(body.project_id)
    if not p:
        raise HTTPException(409, "服务端数据已重置（免费实例重启），历史版本不可用。当前页面仍可继续修改或发布。")
    v = db.get_version(body.project_id, body.version_no)
    if not v:
        raise HTTPException(404, "版本不存在")
    db.update_project_html(body.project_id, v["html"])
    db.add_message(body.project_id, "agent", f"已回滚到 V{body.version_no}。", agent="System", kind="result", extra={"version_no": body.version_no})
    return {"ok": True, "html": v["html"], "version_no": body.version_no}


@app.get("/api/versions/{version_id}/html")
async def version_html(version_id: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT html FROM versions WHERE id=?", (version_id,)).fetchone()
    if not row:
        raise HTTPException(404, "版本不存在")
    return HTMLResponse(row["html"])


# ---------------- publish ----------------

@app.post("/api/publish")
async def publish(body: PublishIn, request: Request):
    allowed, retry = ratelimit.check("publish", ratelimit.client_ip(request), config.RL_GENERATE * 3, config.RL_WINDOW)
    if not allowed:
        return JSONResponse(status_code=429, content={"ok": False, "error": f"发布过于频繁，请 {retry} 秒后再试"})
    # Publishing must not depend on the server still holding this project: the free
    # tier wipes SQLite on restart while the client keeps a mirror. Accept the HTML
    # from the client as a fallback and rebuild the row (self-heal).
    p = db.get_project(body.project_id)
    html = (p["current_html"] if p else "") or (body.html or "")
    if not html:
        raise HTTPException(400, "尚无应用可发布")
    if not p or not (p["current_html"] or ""):
        title_hint = (body.title or (p["name"] if p else "") or "未命名应用")[:80]
        db.ensure_project(body.project_id, title_hint, html)
        p = db.get_project(body.project_id)

    slug = uuid.uuid4().hex[:10]
    latest = db.list_versions(body.project_id)
    db.publish(slug, body.project_id, latest[0]["version_no"] if latest else None, html, body.title or p["name"])
    db.add_message(body.project_id, "agent", f"已发布分享链接：/p/{slug}", agent="System", kind="result", extra={"slug": slug})
    return {"ok": True, "slug": slug, "url": f"/p/{slug}"}


PUBLISH_WRAPPER = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · Mini Atoms 发布页</title>
<style>
html,body{margin:0;height:100%;background:#f5f6f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif}
.bar{height:38px;display:flex;align-items:center;gap:10px;padding:0 14px;background:#fff;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280}
.bar b{color:#111827;font-weight:600}.bar .tag{margin-left:auto;background:#eef2ff;color:#4f46e5;padding:3px 9px;border-radius:999px;font-size:11px}
.bar a{color:#4f46e5;text-decoration:none}
iframe{display:block;width:100%;height:calc(100% - 38px);border:0;background:#fff}
</style></head><body>
<div class="bar"><b>__TITLE__</b><span>由 Mini Atoms 生成</span>
<span class="tag">用户生成内容 · 与本平台无隶属或背书关系</span>
<a href="/" target="_blank" rel="noopener">用 Mini Atoms 构建 →</a></div>
<iframe src="/p/__SLUG__/raw" sandbox="allow-scripts allow-forms allow-modals" referrerpolicy="no-referrer" title="published app"></iframe>
</body></html>"""

# Strict CSP for user-generated content: inline style/script allowed (single-file apps need them),
# but NO external network, NO framing by others, NO base-uri hijack.
PUBLISH_CSP = (
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; "
    "img-src data: blob:; "
    "font-src data:; "
    "connect-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'self'"
)


@app.get("/p/{slug}", response_class=HTMLResponse)
async def published_page(slug: str):
    """Serve a safe wrapper shell. The generated HTML never runs on this origin directly."""
    page = db.get_published(slug)
    if not page:
        return HTMLResponse("<h1>404 - 页面不存在</h1>", status_code=404)
    title = (page["title"] or "Mini Atoms App").replace("<", "&lt;").replace(">", "&gt;")[:80]
    html = PUBLISH_WRAPPER.replace("__TITLE__", title).replace("__SLUG__", slug)
    return HTMLResponse(html, headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-src 'self'; base-uri 'none'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


@app.get("/p/{slug}/raw", response_class=HTMLResponse)
async def published_raw(slug: str):
    """The actual generated document, locked down by CSP and sandboxed by the wrapper iframe."""
    page = db.get_published(slug)
    if not page:
        return HTMLResponse("<h1>404</h1>", status_code=404)
    return HTMLResponse(page["html"], headers={
        "Content-Security-Policy": PUBLISH_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex",
    })


# ---------------- static frontend (production) ----------------

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@app.get("/", response_class=HTMLResponse)
async def index():
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Mini Atoms API</h1><p>Frontend not built yet.</p>")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("p/"):
        raise HTTPException(404, "Not found")
    candidate = (FRONTEND_DIST / full_path).resolve()
    # Never serve files outside the built frontend directory.
    if candidate.is_file() and str(candidate).startswith(str(FRONTEND_DIST.resolve())):
        from fastapi.responses import FileResponse
        return FileResponse(candidate)
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    raise HTTPException(404, "Not found")
