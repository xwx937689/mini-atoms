"""Mini Atoms API - AI-driven web app generation workbench."""
import asyncio
import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import config, database as db, mock
from .llm import LLMClient, LLMError
from .validator import static_check

app = FastAPI(title="Mini Atoms API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS + ["*"],
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
    project_id: str
    prompt: str


class EditIn(BaseModel):
    project_id: str
    instruction: str


class RollbackIn(BaseModel):
    project_id: str
    version_no: int


class PublishIn(BaseModel):
    project_id: str
    title: Optional[str] = None


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
async def generate(body: GenerateIn):
    p = _ok(body.project_id)
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(400, "需求不能为空")

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

        # quality gate + one repair
        if llm.ready:
            report = await llm.review(html)
        else:
            await asyncio.sleep(0.1)
            report = mock.review(html)
        if not report.get("valid", True) or not static_check(html)["valid"]:
            issues = list(report.get("issues") or []) + static_check(html)["issues"]
            steps.append(_activity("Reviewer", "发现质量问题，请求修复", "Review", "；".join(issues[:3])))
            if llm.ready:
                html = await llm.repair(html, issues)
            if not static_check(html)["valid"]:
                raise LLMError("自动修复后仍未通过结构校验：" + "；".join(static_check(html)["issues"][:3]))

        steps.append(_activity("Reviewer", "质量校验通过", "Run check", "HTML 结构完整，脚本可运行"))

        version_no = db.next_version_no(body.project_id)
        name = plan.get("app_name") or p["name"]
        db.update_project_html(body.project_id, html, prompt=prompt, name=name)
        db.add_version(body.project_id, version_no, f"V{version_no}: {name}", html, plan)
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
            "mock_mode": not llm.ready,
        }
    except Exception as e:
        # Only fall back when the LLM transport itself failed (dead key / quota / network).
        # NEVER replace a successfully generated-but-imperfect result with generic mock data.
        if llm.ready and isinstance(e, LLMError) and "模型请求失败" in str(e):
            try:
                plan = plan if 'plan' in locals() else mock.plan(prompt)
                html = mock.build(prompt, plan)
                version_no = db.next_version_no(body.project_id)
                name = plan.get("app_name") or p["name"]
                db.update_project_html(body.project_id, html, prompt=prompt, name=name)
                db.add_version(body.project_id, version_no, f"V{version_no}: {name}", html, plan)
                db.add_message(body.project_id, "agent", "模型服务不可用，已切换到演示模式生成。", agent="System", kind="result", extra={"version_no": version_no})
                return {
                    "ok": True, "project_id": body.project_id, "version_no": version_no,
                    "plan": plan, "html": html, "steps": steps, "mock_mode": True,
                    "fallback": True, "fallback_reason": str(e)[:200],
                }
            except Exception as mock_err:
                e = Exception(f"{e}；演示模式也失败：{mock_err}")
        db.add_message(body.project_id, "agent", f"生成失败：{e}", agent="System", kind="error")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e), "steps": steps})


@app.post("/api/edit")
async def edit_app(body: EditIn):
    p = _ok(body.project_id)
    instruction = body.instruction.strip()
    if not instruction:
        raise HTTPException(400, "修改要求不能为空")
    if not p["current_html"]:
        raise HTTPException(400, "请先生成应用")

    db.add_message(body.project_id, "user", instruction, kind="edit")
    history = db.get_messages(body.project_id)
    steps = [_activity("Mike", "理解修改要求，检查当前版本", "Read context", instruction[:80])]

    try:
        steps.append(_activity("Alex", "在现有版本上应用修改", "Edit code", "保留已有功能"))
        html = await llm.edit(p["original_prompt"], p["current_html"], history, instruction) if llm.ready else mock.edit(p["original_prompt"], p["current_html"], history, instruction)
        check = static_check(html)
        if not check["valid"]:
            if llm.ready:
                html = await llm.repair(html, check["issues"])
                check = static_check(html)
            if not check["valid"]:
                raise LLMError("修改结果校验失败：" + "；".join(check["issues"][:3]))
        steps.append(_activity("Reviewer", "修改后校验通过，保留全部既有功能", "Run check", f"HTML {len(html)} 字符"))

        version_no = db.next_version_no(body.project_id)
        db.update_project_html(body.project_id, html)
        db.add_version(body.project_id, version_no, f"V{version_no}: {instruction[:24]}", html)
        db.add_message(body.project_id, "agent", "修改已完成并生成新版本。", agent="Alex · Engineer", kind="result", extra={"steps": steps, "version_no": version_no})
        return {"ok": True, "version_no": version_no, "html": html, "steps": steps, "mock_mode": not llm.ready}
    except Exception as e:
        # Only fall back on transport failure. A validation failure after a successful model
        # call must NOT wipe the previous good version with generic mock output.
        if llm.ready and isinstance(e, LLMError) and "模型请求失败" in str(e):
            try:
                html = mock.edit(p["original_prompt"], p["current_html"], history, instruction)
                if static_check(html)["valid"]:
                    version_no = db.next_version_no(body.project_id)
                    db.update_project_html(body.project_id, html)
                    db.add_version(body.project_id, version_no, f"V{version_no}: {instruction[:24]}", html)
                    db.add_message(body.project_id, "agent", "模型服务不可用，已用演示模式应用修改。", agent="System", kind="result", extra={"version_no": version_no})
                    return {"ok": True, "version_no": version_no, "html": html, "steps": steps, "mock_mode": True, "fallback": True}
            except Exception as mock_err:
                e = Exception(f"{e}；演示模式也失败：{mock_err}")
        db.add_message(body.project_id, "agent", f"修改失败，已保留上一可用版本：{e}", agent="System", kind="error")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e), "steps": steps})


# ---------------- versions ----------------

@app.get("/api/projects/{project_id}/versions")
async def versions(project_id: str):
    _ok(project_id)
    return db.list_versions(project_id)


@app.post("/api/rollback")
async def rollback(body: RollbackIn):
    p = _ok(body.project_id)
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
async def publish(body: PublishIn):
    p = _ok(body.project_id)
    if not p["current_html"]:
        raise HTTPException(400, "尚无应用可发布")
    slug = uuid.uuid4().hex[:10]
    latest = db.list_versions(body.project_id)
    db.publish(slug, body.project_id, latest[0]["version_no"] if latest else None, p["current_html"], body.title or p["name"])
    db.add_message(body.project_id, "agent", f"已发布分享链接：/p/{slug}", agent="System", kind="result", extra={"slug": slug})
    return {"ok": True, "slug": slug, "url": f"/p/{slug}"}


@app.get("/p/{slug}", response_class=HTMLResponse)
async def published_page(slug: str):
    page = db.get_published(slug)
    if not page:
        return HTMLResponse("<h1>404 - 页面不存在</h1>", status_code=404)
    return HTMLResponse(page["html"])


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
