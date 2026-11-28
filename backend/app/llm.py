"""LLM client: OpenAI-compatible chat completions with streaming support and mock fallback."""
import json
import re
from typing import Any, AsyncIterator, Optional

import httpx

from . import config, prompts


class LLMError(Exception):
    pass


# deepseek-chat hard-caps output at 8192 tokens; asking for more does not help and makes the
# model believe it has room to write a very long file (it does not - it gets cut off).
MODEL_MAX_OUTPUT = 8192
# Roughly what 8192 tokens buys in this density of HTML/CSS/JS. Used to enforce scope.
SAFE_HTML_CHARS = 12000


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
        # Braces must balance in valid JS. A small positive surplus means the file was
        # cut inside a block. A negative surplus or zero is fine (braces in strings/regex
        # can add a little noise, hence only flagging a surplus).
        if script.count("{") - script.count("}") > 0:
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
        raw = await self.complete(prompts.SYSTEM_BUILDER, user, temperature=0.6, max_tokens=MODEL_MAX_OUTPUT)
        if looks_truncated(raw):
            # Telling the model "be shorter" is not enough - it re-plans the same scope.
            # Cut the plan itself so the retry has materially less to build.
            feats = list(plan.get("features") or [])
            reduced = dict(plan)
            reduced["features"] = feats[:4]
            reduced["pages"] = (plan.get("pages") or [])[:2]
            reduced["components"] = (plan.get("components") or [])[:5]
            retry = (
                f"原始需求：\n{prompt}\n\n"
                f"上一轮输出在 </html> 之前被截断了，必须缩减范围。只实现下列规划（其余放弃）：\n"
                f"{json.dumps(reduced, ensure_ascii=False, indent=2)}\n\n"
                "[硬性要求] 完整 HTML 控制在 11,000 字符以内。只做上面列出的功能，"
                "宁可少做也要保证从 <!DOCTYPE html> 到 </html> 完整闭合。"
                "未实现的功能不要画入口，不要写半截函数。"
            )
            raw = await self.complete(prompts.SYSTEM_BUILDER, retry, temperature=0.35, max_tokens=MODEL_MAX_OUTPUT)
            if looks_truncated(raw):
                # Still cut off. Closing tags cannot restore lost JS logic,
                # so refuse outright instead of letting clean_html paper over it.
                raise LLMError("模型连续两次输出被截断，未生成完整文档。已放弃本次结果，请重试或简化需求。")
        html = clean_html(raw)
        # Oversized output makes every later edit prone to truncation.
        # Rewrite once, more compactly, while the requirement is still in hand.
        if len(html) > 15000:
            shrink = user + (
                f"\n\n[IMPORTANT] The previous result was {len(html)} characters, too long for stable "
                "editing. Rewrite the SAME app in under 12,000 characters: keep every feature, but use "
                "terse CSS and compact JS. The file MUST be complete and end with </script></body></html>."
            )
            shrunk = await self.complete(prompts.SYSTEM_BUILDER, shrink, temperature=0.3, max_tokens=MODEL_MAX_OUTPUT)
            small = clean_html(shrunk)
            if not looks_truncated(shrunk) and 2000 < len(small) < len(html):
                html = small
        return html

    async def edit(self, original_prompt: str, current_html: str, history: list[dict], instruction: str) -> str:
        recent = "\n".join(
            f"{m.get('agent') or m.get('role')}: {m.get('content', '')[:400]}" for m in history[-8:]
        )
        # Give the model a concrete character budget instead of a vague "keep it short".
        # If the base file is already near the ceiling, the edit must also trim, not just add.
        cur_len = len(current_html)
        over = cur_len > SAFE_HTML_CHARS
        budget = SAFE_HTML_CHARS if over else cur_len + 1200
        if over:
            budget_note = (
                f"\n\n[输出预算] 当前文件 {cur_len} 字符，已接近上限。本次输出必须 ≤ {budget} 字符。"
                "这意味着要边加边减：加入新功能的同时，删掉冗余样式和重复代码，保持所有已有功能可用。"
            )
        else:
            budget_note = (
                f"\n\n[输出预算] 当前文件 {cur_len} 字符。本次输出 ≤ {budget} 字符，"
                "优先复用现有 CSS 类，不要重写整个页面。"
            )
        user = (
            f"原始需求：\n{original_prompt}\n\n"
            f"近期对话（按时间顺序）：\n{recent or '（无）'}\n\n"
            f"当前 HTML（完整）：\n{current_html}\n\n"
            f"用户最新修改要求：\n{instruction}\n\n"
            "请输出修改后的完整 HTML。" + budget_note
        )
        raw = await self.complete(prompts.SYSTEM_EDITOR, user, temperature=0.5, max_tokens=MODEL_MAX_OUTPUT)
        for attempt in range(2):
            if not looks_truncated(raw):
                break
            retry = user + (
                f"\n\n[IMPORTANT] 上一轮输出在 </html> 之前被截断了。"
                f"这次必须把总长度控制在 {budget} 字符以内：只做最小可用实现（加控件 + 最简逻辑，"
                "复用现有样式），并在加入新功能时删掉等量的冗余代码。"
                "务必完整闭合到 </script></body></html>。"
            )
            raw = await self.complete(prompts.SYSTEM_EDITOR, retry, temperature=0.3, max_tokens=MODEL_MAX_OUTPUT)
        if looks_truncated(raw):
            # Never let clean_html close tags on a half-written script and call it done.
            raise LLMError("模型连续多次输出被截断，修改结果不完整。已放弃本次修改，原版本保留。")
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
        return clean_html(await self.complete(prompts.SYSTEM_REPAIR, user, temperature=0.4, max_tokens=MODEL_MAX_OUTPUT))
