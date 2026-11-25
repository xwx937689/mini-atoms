"""Multi-dimensional quality scoring for generated single-file HTML apps.

Static and deterministic (no LLM mood swings). Complements the pass/fail gate in
validator.py by answering "HOW GOOD is the result", not just "does it run".
"""
import re
from typing import Any

# Dimension weights (sum = 100)
WEIGHTS = {
    "requirement": 25,   # planned features actually present in code
    "interaction": 25,   # real event bindings vs visible controls
    "data": 20,          # structured mock data instead of toy placeholders
    "visual": 20,        # layout breadth, charts, responsive, color system
    "robustness": 10,    # no storage APIs, no alert, no external deps
}


def _has(haystack: str, needle: str) -> bool:
    return needle.lower() in haystack.lower()


def score_requirement(html: str, plan: dict | None) -> tuple[float, str]:
    """How many planned features appear to be implemented (keyword heuristics)."""
    features = (plan or {}).get("features") or []
    if not features:
        return 60.0, "无可核对的规划清单，按中性分计"
    code = html.lower()
    # Chinese features: count distinct 2-char tokens that survived into the code.
    hits = 0
    for f in features:
        tokens = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}", str(f)) if len(t) >= 2]
        if not tokens:
            continue
        if any(t.lower() in code for t in tokens[:3]):
            hits += 1
    ratio = hits / len(features)
    return round(ratio * 100, 1), f"规划 {len(features)} 项功能，代码命中 {hits} 项"


def score_interaction(html: str) -> tuple[float, str]:
    """Density of real event bindings relative to visible interactive controls."""
    bindings = len(re.findall(r"onclick\s*=|addEventListener\s*\(|\.onclick\s*=", html))
    controls = len(re.findall(r"<button\b|<input\b|<select\b|<textarea\b|class=[\"'][^\"']*(?:nav-item|card|tab|item)", html, re.I))
    controls = max(controls, 1)
    ratio = min(bindings / controls, 1.0)
    score = min(100.0, 40 + ratio * 60)
    return round(score, 1), f"{bindings} 处事件绑定 / {controls} 个可见控件"


def score_data(html: str) -> tuple[float, str]:
    """Presence of structured mock data rather than single-word placeholders."""
    score = 30.0
    signals = []
    if re.search(r"const\s+\w+\s*=\s*\[[\s\S]{200,}?\]", html):
        score += 25; signals.append("数组数据集")
    if re.search(r"\{\s*\w+\s*:", html) and len(re.findall(r"\w+\s*:", html)) > 12:
        score += 20; signals.append("结构化对象")
    if len(re.findall(r"[\u4e00-\u9fff]{2,6}", html)) > 40:
        score += 15; signals.append("中文业务语料")
    if re.search(r"Math\.random|new Date\(|toISOString", html):
        score += 10; signals.append("动态数据")
    return min(100.0, score), "、".join(signals) or "几乎无模拟数据"


def score_visual(html: str) -> tuple[float, str]:
    """Layout breadth, charts, responsive rules, color consistency."""
    score = 30.0
    signals = []
    css_lines = len(re.findall(r"\{[^{}]*\}", html))
    if css_lines > 25:
        score += 20; signals.append("样式丰富")
    if re.search(r"<svg|<canvas|chart|progress|bar", html, re.I):
        score += 20; signals.append("图表元素")
    if re.search(r"@media|grid-template|flex", html, re.I):
        score += 15; signals.append("响应式/布局")
    if re.search(r"--[\w-]+:|#[0-9a-fA-F]{6}", html):
        score += 15; signals.append("配色系统")
    return min(100.0, score), "、".join(signals) or "视觉单薄"


def score_robustness(html: str) -> tuple[float, str]:
    """Absence of known-fragile patterns in the sandboxed iframe."""
    score = 100.0
    penalties = []
    if re.search(r"localStorage|sessionStorage|indexedDB", html, re.I):
        score -= 35; penalties.append("浏览器存储API")
    if re.search(r"\balert\s*\(", html):
        score -= 20; penalties.append("alert弹窗")
    if re.search(r"<script[^>]+src=|<link[^>]+stylesheet", html, re.I):
        score -= 25; penalties.append("外部依赖")
    if "```" in html:
        score -= 30; penalties.append("代码围栏")
    return max(0.0, score), "无脆弱模式" if not penalties else "、".join(penalties)


def score_app(html: str, plan: dict | None = None) -> dict[str, Any]:
    dims = {
        "requirement": score_requirement(html, plan),
        "interaction": score_interaction(html),
        "data": score_data(html),
        "visual": score_visual(html),
        "robustness": score_robustness(html),
    }
    total = sum(dims[k][0] * WEIGHTS[k] for k in WEIGHTS) / 100
    return {
        "total": round(total, 1),
        "grade": "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 55 else "D",
        "dimensions": [
            {"key": k, "label": {
                "requirement": "需求覆盖", "interaction": "交互实现", "data": "数据真实感",
                "visual": "视觉完整", "robustness": "健壮性",
            }[k], "weight": WEIGHTS[k], "score": dims[k][0], "note": dims[k][1]}
            for k in WEIGHTS
        ],
    }
