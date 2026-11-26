"""SQLite persistence for projects, conversations, versions and published pages."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from .config import DB_PATH

_local = threading.local()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except BaseException:
        # Explicit rollback: never rely on close() to discard a half-written transaction.
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                original_prompt TEXT NOT NULL DEFAULT '',
                current_html    TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                agent       TEXT NOT NULL DEFAULT '',
                kind        TEXT NOT NULL DEFAULT 'text',
                content     TEXT NOT NULL,
                extra       TEXT NOT NULL DEFAULT '{}',
                created_at  TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS versions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id  TEXT NOT NULL,
                version_no  INTEGER NOT NULL,
                label       TEXT NOT NULL,
                html        TEXT NOT NULL,
                plan        TEXT NOT NULL DEFAULT '{}',
                created_at  TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS published (
                slug        TEXT PRIMARY KEY,
                project_id  TEXT NOT NULL,
                version_no  INTEGER,
                html        TEXT NOT NULL,
                title       TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_msg_project ON messages(project_id, id);
            CREATE INDEX IF NOT EXISTS idx_ver_project ON versions(project_id, version_no);
            """
        )


# ---------- projects ----------

def create_project(project_id: str, name: str, prompt: str = "") -> dict:
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO projects(id,name,original_prompt,current_html,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (project_id, name, prompt, "", now, now),
        )
    return {
        "id": project_id,
        "name": name,
        "original_prompt": prompt,
        "current_html": "",
        "created_at": now,
        "updated_at": now,
        "version_count": 0,
    }


def get_project(project_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return dict(row) if row else None


def ensure_project(project_id: str, name: str = "未命名应用", html: str = "", prompt: str = "") -> bool:
    """Recreate a project row that disappeared (free-tier restart wipes the DB).

    The client keeps a mirror of every project, so when it acts on a project the
    server no longer knows about, we can rebuild the row from the HTML it sends.
    Never overwrites an existing row; only fills an empty current_html.

    Returns True if the project had to be recreated.
    """
    now = _now()
    with get_conn() as conn:
        row = conn.execute("SELECT id, current_html FROM projects WHERE id=?", (project_id,)).fetchone()
        if row:
            if html and not (row["current_html"] or ""):
                conn.execute(
                    "UPDATE projects SET current_html=?, updated_at=? WHERE id=?",
                    (html, now, project_id),
                )
            return False
        conn.execute(
            "INSERT INTO projects(id,name,original_prompt,current_html,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (project_id, name, prompt, html, now, now),
        )
    return True


def list_projects() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM versions v WHERE v.project_id=p.id) AS version_count "
            "FROM projects p ORDER BY p.updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def update_project_html(project_id: str, html: str, prompt: Optional[str] = None, name: Optional[str] = None):
    with get_conn() as conn:
        if prompt is not None and name is not None:
            conn.execute(
                "UPDATE projects SET current_html=?, original_prompt=?, name=?, updated_at=? WHERE id=?",
                (html, prompt, name, _now(), project_id),
            )
        elif prompt is not None:
            conn.execute(
                "UPDATE projects SET current_html=?, original_prompt=?, updated_at=? WHERE id=?",
                (html, prompt, _now(), project_id),
            )
        else:
            conn.execute(
                "UPDATE projects SET current_html=?, updated_at=? WHERE id=?",
                (html, _now(), project_id),
            )


def delete_project(project_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM versions WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM published WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))


# ---------- messages ----------

def add_message(project_id: str, role: str, content: str, agent: str = "", kind: str = "text", extra: Optional[dict] = None) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages(project_id,role,agent,kind,content,extra,created_at) VALUES(?,?,?,?,?,?,?)",
            (project_id, role, agent, kind, content, json.dumps(extra or {}, ensure_ascii=False), _now()),
        )
        row = conn.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (_now(), project_id))
        return dict(row)


def get_messages(project_id: str, limit: int = 60) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, limit)
        ).fetchall()
        out = []
        for r in reversed(rows):
            d = dict(r)
            try:
                d["extra"] = json.loads(d.get("extra") or "{}")
            except Exception:
                d["extra"] = {}
            out.append(d)
        return out


# ---------- versions ----------

def next_version_no(project_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COALESCE(MAX(version_no),0)+1 AS n FROM versions WHERE project_id=?", (project_id,)).fetchone()
        return int(row["n"])


def add_version(project_id: str, version_no: int, label: str, html: str, plan: Optional[dict] = None) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO versions(project_id,version_no,label,html,plan,created_at) VALUES(?,?,?,?,?,?)",
            (project_id, version_no, label, html, json.dumps(plan or {}, ensure_ascii=False), _now()),
        )
        row = conn.execute("SELECT id,project_id,version_no,label,created_at,length(html) AS html_len FROM versions WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)


def save_project_version(
    project_id: str,
    version_no: int,
    label: str,
    html: str,
    plan: Optional[dict] = None,
    prompt: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    """Atomically update current_html AND append a version row.

    Both writes happen inside one SQLite transaction: if either fails, neither is
    persisted, so we can never end up with "html changed but no version recorded".
    """
    now = _now()
    with get_conn() as conn:
        if prompt is not None and name is not None:
            conn.execute(
                "UPDATE projects SET current_html=?, original_prompt=?, name=?, updated_at=? WHERE id=?",
                (html, prompt, name, now, project_id),
            )
        else:
            conn.execute(
                "UPDATE projects SET current_html=?, updated_at=? WHERE id=?",
                (html, now, project_id),
            )
        cur = conn.execute(
            "INSERT INTO versions(project_id,version_no,label,html,plan,created_at) VALUES(?,?,?,?,?,?)",
            (project_id, version_no, label, html, json.dumps(plan or {}, ensure_ascii=False), now),
        )
        row = conn.execute(
            "SELECT id,project_id,version_no,label,created_at,length(html) AS html_len FROM versions WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
        version = dict(row)
    return version


def list_versions(project_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id,project_id,version_no,label,created_at,length(html) AS html_len FROM versions "
            "WHERE project_id=? ORDER BY version_no DESC", (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_version(project_id: str, version_no: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM versions WHERE project_id=? AND version_no=?", (project_id, version_no)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["plan"] = json.loads(d.get("plan") or "{}")
        except Exception:
            d["plan"] = {}
        return d


# ---------- published ----------

def publish(slug: str, project_id: str, version_no: Optional[int], html: str, title: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO published(slug,project_id,version_no,html,title,created_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(slug) DO UPDATE SET html=excluded.html, title=excluded.title, created_at=excluded.created_at",
            (slug, project_id, version_no, html, title, _now()),
        )


def get_published(slug: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM published WHERE slug=?", (slug,)).fetchone()
        return dict(row) if row else None
