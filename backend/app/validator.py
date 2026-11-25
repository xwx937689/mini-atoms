"""Fast structural validation of generated HTML. No heavy dependencies."""
import re
from typing import Any


def static_check(html: str) -> dict[str, Any]:
    issues: list[str] = []
    s = (html or "").strip()
    low = s.lower()

    if len(s) < 400:
        issues.append("HTML 内容过短，疑似生成失败")
    if not (low.startswith("<!doctype") or low.startswith("<html")):
        issues.append("缺少 <!DOCTYPE html> 或 <html> 起始标签")
    if "<html" not in low or "</html>" not in low:
        issues.append("html 标签不完整")
    if "<head" not in low or "</head>" not in low:
        issues.append("head 标签不完整")
    if "<body" not in low or "</body>" not in low:
        issues.append("body 标签不完整")
    if "<style" not in low:
        issues.append("未找到内联 CSS")
    has_script = "<script" in low
    has_interaction = bool(re.search(r"onclick|addEventListener|function\s+\w+\s*\(", s))
    if not has_script and not has_interaction:
        issues.append("未找到可执行的脚本段或交互逻辑")
    if low.count("<script") != low.count("</script>"):
        issues.append("script 标签数量不匹配")
    if "```" in s:
        issues.append("残留 Markdown 代码围栏")
    if re.search(r"\bTODO\b|lorem ipsum|占位符文本", s, flags=re.I):
        issues.append("存在占位符/TODO 文本")

    bad_open = len(re.findall(r"<div\b", low)) - len(re.findall(r"</div>", low))
    if bad_open > 3:
        issues.append(f"div 标签明显不闭合（差 {bad_open} 个）")

    # Decorative controls: elements with pointer cursor / nav-item class but no click binding anywhere.
    has_any_click = bool(re.search(r"onclick|addEventListener|\.onclick|querySelectorAll", s))
    nav_items = re.findall(r'class="[^"]*(?:nav-item|nav_link|side-item|menu-item)[^"]*"', s)
    if nav_items and not has_any_click:
        issues.append(f"存在 {len(nav_items)} 个导航项但没有任何点击绑定（死菜单）")
    pointer_divs = len(re.findall(r'<div[^>]*class="[^"]*"[^>]*style="[^"]*cursor\s*:\s*pointer', s))
    if pointer_divs >= 3 and not has_any_click:
        issues.append(f"存在 {pointer_divs} 个可点击样式的 div 但没有点击绑定")

    return {"valid": len(issues) == 0, "issues": issues, "score": max(0, 100 - len(issues) * 12)}
