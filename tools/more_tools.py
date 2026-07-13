"""完整工具集：edit / grep / glob（Day6）+ web_fetch / task_list（Day7）。

web_fetch 叠加出站白名单 + <external> 边界（Day10 注入防护）。
"""
from __future__ import annotations
import subprocess
from pathlib import Path
from .base import Tool
from .guard import wrap_external, check_host


# --- edit：search-replace（最稳策略：old 必须唯一，否则提示模型调整）---
def _edit(path: str, old: str = "", new: str = "") -> str:
    """精确字符串替换：old 在文件中恰好出现 1 次时才替换。

    0 次 → 提示模型照抄原文（含缩进）；多次 → 提示扩展 old 使其唯一。
    这是比 unified diff 更稳的策略——不依赖行号，模型只需要引用原文片段。
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    count = text.count(old)
    if count == 0:
        return f"[失败] 未找到待替换文本，请照抄文件原文（含缩进）。path={path}"
    if count > 1:
        return f"[失败] old 在文件中出现 {count} 次，不唯一；请扩大 old 片段使其唯一。"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new, 1))
    return f"已在 {path} 完成 1 处替换。"


# --- grep：基于 ripgrep，搜内容（与 glob 互补：grep 搜内容，glob 搜路径）---
def _grep(pattern: str, path: str = ".", max_lines: int = 100) -> str:
    """在文件中搜索匹配 pattern 的行，返回 文件:行号:内容 格式。

    Args:
        pattern: ripgrep 搜索模式（正则）
        path: 搜索路径（文件或目录）
        max_lines: 最大返回行数，超出则截断并提示
    """
    try:
        p = subprocess.run(
            ["rg", "--line-number", "--no-heading", pattern, path],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return "[失败] 未找到 rg，请先安装 ripgrep。"
    if p.returncode not in (0, 1):  # 1 = 无匹配，属正常
        return f"[grep 出错] {p.stderr.strip()}"
    lines = p.stdout.splitlines()
    if not lines:
        return f"[无匹配] pattern={pattern}"
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n... [共 {len(lines)} 行，已截断前 {max_lines} 行]"
    return "\n".join(lines)


# --- glob：按文件名模式找文件（与 grep 互补：grep 搜内容，glob 搜路径）---
def _glob(pattern: str, max_items: int = 100) -> str:
    """递归匹配文件名，返回相对路径列表。

    Args:
        pattern: 通配模式（如 *.py、**/*.md），支持 pathlib rglob 语法
        max_items: 最大返回数，超出则截断并提示
    """
    paths = [str(p) for p in Path(".").rglob(pattern) if p.is_file()]
    if not paths:
        return f"[无匹配] pattern={pattern}"
    if len(paths) > max_items:
        return "\n".join(paths[:max_items]) + f"\n... [共 {len(paths)} 个，已截断前 {max_items} 个]"
    return "\n".join(paths)


# --- web_fetch：URL -> markdown，控 token 预算 ---
def _web_fetch(url: str, max_tokens: int = 2000) -> str:
    """抓取 URL → HTML 转 markdown → 按 token 预算截断。

    三步流水线：httpx 抓取 → markdownify 转换 → truncate 截断。
    截断是核心——否则一次抓取就能撑爆上下文窗口。

    Day10 注入防护：
      1. 出站白名单 —— 只放行 ALLOW_HOSTS 内的域名
      2. 外部内容边界 —— 返回内容包 <external> 标签
    """
    import httpx
    from markdownify import markdownify as md
    from agent.context import truncate_observation

    # 出站白名单检查
    rejection = check_host(url)
    if rejection:
        return rejection

    try:
        resp = httpx.get(url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return f"[抓取失败] {e}"
    text = md(resp.text)
    text = truncate_observation(text, max_chars=max_tokens * 4)
    # 注入防护：外部内容包边界
    return wrap_external(text, url)


# --- task_list（TodoWrite）：自维护待办，提升长任务成功率 ---
_tasks: list[dict] = []


def _task_list(action: str, items: list | None = None) -> str:
    """维护结构化待办清单，作为模型的 scratchpad。

    action: add | update | complete | list
    items: add 时为任务描述字符串列表；update/complete 时为含 id 的对象列表。
    """
    global _tasks
    items = items or []
    if action == "add":
        for item in items:
            _tasks.append({"id": len(_tasks) + 1,
                           "content": str(item), "status": "pending"})
        return f"已添加 {len(items)} 个任务，当前共 {len(_tasks)} 项。"
    elif action == "update":
        for item in items:
            for t in _tasks:
                if t["id"] == item.get("id"):
                    t["content"] = item.get("content", t["content"])
        return f"已更新 {len(items)} 个任务。"
    elif action == "complete":
        for item in items:
            tid = item.get("id") if isinstance(item, dict) else item
            for t in _tasks:
                if t["id"] == tid:
                    t["status"] = "completed"
        return f"已完成 {len(items)} 个任务。"
    elif action == "list":
        if not _tasks:
            return "当前无待办任务。"
        lines = []
        for t in _tasks:
            mark = "✅" if t["status"] == "completed" else "⏳"
            lines.append(f"{mark} [{t['id']}] {t['content']}")
        return "\n".join(lines)
    return f"未知 action: {action}，支持 add/update/complete/list。"


edit_tool = Tool(
    name="edit",
    description=(
        "精确替换文件中的文本（search-replace）。"
        "old 必须是文件中恰好出现 1 次的原文片段（含缩进），否则替换会失败。"
        "如果 old 出现 0 次或多次，请照抄原文扩大 old 范围使其唯一。"
    ),
    parameters={"type": "object",
                "properties": {"path": {"type": "string", "description": "要编辑的文件路径"},
                               "old": {"type": "string", "description": "要替换的原文本（必须照抄原文，含缩进，且在文件中唯一）"},
                               "new": {"type": "string", "description": "替换后的新文本"}},
                "required": ["path", "old", "new"]},
    run=_edit,
)
grep_tool = Tool(
    name="grep",
    description=(
        "在文件中搜索匹配 pattern 的行，返回 文件:行号:内容 格式。"
        "基于 ripgrep，支持正则表达式。用于搜代码内容（与 glob 互补：grep 搜内容，glob 搜文件名）。"
    ),
    parameters={"type": "object",
                "properties": {"pattern": {"type": "string", "description": "搜索模式（正则表达式，如 def main、TODO、import os）"},
                               "path": {"type": "string", "description": "搜索路径，可以是文件或目录，默认当前目录"}},
                "required": ["pattern"]},
    run=_grep,
)
glob_tool = Tool(
    name="glob",
    description=(
        "按文件名通配模式递归查找文件，返回相对路径列表。"
        "用于搜文件路径（与 grep 互补：grep 搜内容，glob 搜文件名）。"
        "模式示例：*.py（所有 Python 文件）、**/*.md（所有 Markdown 文件）、**/test_*.py（所有测试文件）。"
    ),
    parameters={"type": "object",
                "properties": {"pattern": {"type": "string", "description": "文件名通配模式（如 *.py、**/*.md、**/test_*.py）"}},
                "required": ["pattern"]},
    run=_glob,
)
web_fetch_tool = Tool("web_fetch", "抓取 URL 并转为 markdown（受 token 预算限制）。",
                      {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]}, _web_fetch)
task_list_tool = Tool("task_list", "维护任务待办清单（add/update/complete）。",
                      {"type": "object", "properties": {"action": {"type": "string"},
                       "items": {"type": "array"}}, "required": ["action"]}, _task_list)


# --- remember：模型自主写入持久记忆（Day11）---
def _remember(note: str) -> str:
    """当用户告诉你一条应长期记住的项目约定/偏好/关键决策时，调用它写入持久记忆。"""
    from agent.memory import Memory
    Memory("MEMORY.md").write(note)
    return "已记住：" + note


remember_tool = Tool(
    name="remember",
    description="当用户告诉你一条应长期记住的项目约定 / 偏好 / 关键决策时，调用它写入持久记忆。",
    parameters={"type": "object",
                "properties": {"note": {"type": "string", "description": "要记住的内容"}},
                "required": ["note"]},
    run=_remember,
)
