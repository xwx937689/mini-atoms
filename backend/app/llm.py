"""LLM client: OpenAI-compatible chat completions with streaming support and mock fallback."""
import json
import re
from typing import Any, AsyncIterator, Optional

import httpx

from . import config, prompts


class LLMError(Exception):
    pass


def _extract_json(text: str) -> Any:
    """Best-effort JSON extraction from a model reply."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    # find first balanced object
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except Exception:
                        break
    raise LLMError("无法从模型返回中解析 JSON")


def clean_html(text: str) -> str:
    """Strip markdown fences and surrounding chatter from generated HTML."""
    s = (text or "").strip()
    s = re.sub(r"^```(?:html|htm)?\s*\n?", "", s, flags=re.I)
    s = re.sub(r"\n?```\s*$", "", s)
    s = s.strip()
    low = s.lower()
    if not low.startswith("<!doctype") and not low.startswith("<html"):
        i = low.find("<!doctype")
        if i < 0:
            i = low.find("<html")
        if i >= 0:
            s = s[i:]
            low = s.lower()

    # Repair truncated output. Close INNER tags first (script/style), then outer ones,
    # otherwise "</script>" ends up after "</html>" and the document breaks.
    low = s.lower()
    if low.count("<style") > low.count("</style>"):
        s += "\n</style>"
    if low.count("<script") > low.count("</script>"):
        s += "\n</script>"
    low = s.lower()
    if "<head" in low and "</head>" not in low:
        s += "\n</head>"
    if "<body" in low and "</body>" not in low:
        s += "\n</body>"
    if "<html" in low and "</html>" not in low:
        s += "\n</html>"
    return s.strip()


def looks_truncated(text: str) -> bool:
    """Detect output that was cut off mid-stream rather than merely stylistically short.

    Runs on RAW model text (before clean_html repairs it), because repair masks the evidence.
    """
    s = (text or "").strip()
    if not s:
        return True
    low = s.lower()
    if "</html>" not in low:
        return True
    if low.count("<script") != low.count("</script>"):
        return True
    if low.count("<style") != low.count("</style>"):
        return True
    if "<script" in low:
        script = s[low.rfind("<script"):]
        if script.count("{") - script.count("}") > 2:
            return True
    return False


class LLMClient:
    def __init__(self):
        self.base = config.LLM_BASE_URL
        self.key = config.LLM_API_KEY
        self.model = config.LLM_MODEL
        self.ready = config.llm_ready()

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    async def complete(self, system: str, user: str, temperature: float = 0.7, max_tokens: int = 8000) -> str:
        if not self.ready:
            raise LLMError("未配置 API Key")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "effort": "low",
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(config.LLM_TIMEOUT, connect=15.0)) as client:
            r = await client.post(f"{self.base}/chat/completions", headers=self._headers(), json=payload)
            if r.status_code >= 400:
                raise LLMError(f"模型请求失败 {r.status_code}: {r.text[:300]}")
            data = r.json()
            try:
                return data["choices"][0]["message"]["content"] or ""
            except Exception:
                raise LLMError(f"模型返回格式异常: {str(data)[:300]}")

    async def plan(self, prompt: str, history: list[dict]) -> dict:
        history_text = "\n".join(
            f"{m.get('agent') or m.get('role')}: {m.get('content', '')[:300]}" for m in history[-6:]
        )
        user = f"用户需求：\n{prompt}\n\n近期对话：\n{history_text or '（无）'}"
        result = await self.complete(prompts.SYSTEM_PLANNER, user, temperature=0.3, max_tokens=2000)
        d = _extract_json(result)
        if not isinstance(d, dict):
            raise LLMError("规划结果不是 JSON 对象")
        for key in ("app_name", "summary", "layout", "style"):
            d.setdefault(key, "")
        for key in ("pages", "features", "components", "data", "acceptance"):
            if not isinstance(d.get(key), list):
                d[key] = []
        return d

    async def build(self, prompt: str, plan: dict) -> str:
        user = f"原始需求：\n{prompt}\n\n已确认的应用规划：\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n请生成完整 HTML。"
        raw = await self.complete(prompts.SYSTEM_BUILDER, user, temperature=0.6, max_tokens=32000)
        if looks_truncated(raw):
            # Truncation is not a style issue - retry with explicit pressure to cut scope.
            retry = user + (
                "\n\n[IMPORTANT] Your previous answer was CUT OFF before </html>. "
                "Regenerate a SHORTER, fully-closed document: implement only the 3-4 core features, "
                "use terse CSS, and make sure the file ends with </script></body></html>."
            )
            raw = await self.complete(prompts.SYSTEM_BUILDER, retry, temperature=0.4, max_tokens=32000)
        html = clean_html(raw)
        # Oversized output makes every later edit prone to truncation.
        # Rewrite once, more compactly, while the requirement is still in hand.
        if len(html) > 15000:
            shrink = user + (
                f"\n\n[IMPORTANT] The previous result was {len(html)} characters, too long for stable "
                "editing. Rewrite the SAME app in under 12,000 characters: keep every feature, but use "
                "terse CSS and compact JS. The file MUST be complete and end with </script></body></html>."
            )
            shrunk = await self.complete(prompts.SYSTEM_BUILDER, shrink, temperature=0.3, max_tokens=32000)
            small = clean_html(shrunk)
            if not looks_truncated(shrunk) and 2000 < len(small) < len(html):
                html = small
        return html

    async def edit(self, original_prompt: str, current_html: str, history: list[dict], instruction: str) -> str:
        recent = "\n".join(
            f"{m.get('agent') or m.get('role')}: {m.get('content', '')[:400]}" for m in history[-8:]
        )
        user = (
            f"原始需求：\n{original_prompt}\n\n"
            f"近期对话（按时间顺序）：\n{recent or '（无）'}\n\n"
            f"当前 HTML（完整）：\n{current_html}\n\n"
            f"用户最新修改要求：\n{instruction}\n\n"
            "请输出修改后的完整 HTML。"
        )
        raw = await self.complete(prompts.SYSTEM_EDITOR, user, temperature=0.5, max_tokens=32000)
        for attempt in range(2):
            if not looks_truncated(raw):
                break
            retry = user + (
                "\n\n[IMPORTANT] Your previous answer was CUT OFF before </html>. "
                "Return the COMPLETE document, keep total size under ~12,000 characters, "
                "and preserve every existing feature. The file MUST end with </script></body></html>."
            )
            raw = await self.complete(prompts.SYSTEM_EDITOR, retry, temperature=0.3, max_tokens=32000)
        return clean_html(raw)

    async def review(self, html: str) -> dict:
        """Model review. Never silently converts failure into pass.

        Returns {"status": "ok"|"error", "valid", "issues", "score", "error"}.
        When status == "error" the caller must treat the result as UNVERIFIED,
        not as approved.
        """
        try:
            result = await self.complete(
                prompts.SYSTEM_REVIEWER,
                f"待检查 HTML：\n{html[:20000]}",
                temperature=0.1,
                max_tokens=800,
            )
            d = _extract_json(result)
            if not isinstance(d, dict):
                return {"status": "error", "valid": False, "issues": [], "score": 0,
                        "error": "审查返回不是 JSON 对象"}
            if not isinstance(d.get("valid"), bool):
                return {"status": "error", "valid": False, "issues": [], "score": 0,
                        "error": "审查结果缺少合法 valid 字段"}
            issues = d.get("issues")
            if not isinstance(issues, list):
                issues = []
            try:
                score = float(d.get("score", 0))
            except (TypeError, ValueError):
                score = 0.0
            return {"status": "ok", "valid": bool(d["valid"]), "issues": [str(i) for i in issues], "score": score}
        except Exception as e:
            return {"status": "error", "valid": False, "issues": [], "score": 0, "error": str(e)[:200]}

    async def repair(self, html: str, issues: list[str]) -> str:
        user = f"问题清单：\n{json.dumps(issues, ensure_ascii=False)}\n\n有问题的 HTML：\n{html}"
        return clean_html(await self.complete(prompts.SYSTEM_REPAIR, user, temperature=0.4, max_tokens=32000))
