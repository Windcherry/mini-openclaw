"""会话持久化（Day11+）：多版本保存 / 列表选择 / 导出对话历史。

每个会话保存为独立的时间戳文件（.mini-openclaw/sessions/session_*.json），
同时维护 last.json 指向最新一次，兼容快速恢复。
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path(".mini-openclaw/sessions")
MAX_SESSIONS = 50  # 最多保留 N 个会话文件


def _ensure_dir() -> Path:
    DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_DIR


def _preview(messages: list[dict], max_len: int = 60) -> str:
    """提取第一条用户消息作为会话预览。"""
    for m in messages:
        if m.get("role") == "user" and m.get("content", "").strip():
            text = m["content"].strip()
            if len(text) > max_len:
                text = text[:max_len] + "…"
            return text
    return "（空会话）"


def _clean_messages(messages: list[dict[str, Any]]) -> list[dict]:
    """清洗消息列表，移除不可 JSON 序列化的字段。"""
    clean = []
    for m in messages:
        c = {"role": m["role"], "content": m.get("content", "")}
        if "name" in m:
            c["name"] = m["name"]
        if "tool_calls" in m:
            c["tool_calls"] = [
                {"id": tc.get("id", ""), "name": tc["name"],
                 "arguments": tc.get("arguments", {})}
                for tc in m["tool_calls"]
            ]
        if "tool_call_id" in m:
            c["tool_call_id"] = m["tool_call_id"]
        clean.append(c)
    return clean


def save_session(messages: list[dict[str, Any]], system_prompt: str = "") -> str:
    """保存会话为时间戳文件，同时更新 last.json。返回 session_id（文件名 stem）。"""
    _ensure_dir()
    now = datetime.now()
    session_id = f"session_{now.strftime('%Y%m%d_%H%M%S')}_{now.microsecond:06d}"
    target = DEFAULT_DIR / f"{session_id}.json"
    # 极端情况：同一微秒内两次保存，追加后缀
    n = 1
    while target.exists():
        n += 1
        target = DEFAULT_DIR / f"{session_id}_{n}.json"
    if n > 1:
        session_id = target.stem

    data = {
        "session_id": session_id,
        "timestamp": now.isoformat(),
        "system_prompt": system_prompt,
        "messages": _clean_messages(messages),
        "message_count": len(messages),
        "preview": _preview(messages),
    }
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 更新 last.json
    last = DEFAULT_DIR / "last.json"
    last.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 清理超量旧文件
    _prune()
    return session_id


def _prune() -> None:
    """删除最旧的会话文件，保持不超过 MAX_SESSIONS 个。"""
    files = sorted(DEFAULT_DIR.glob("session_*.json"), key=lambda p: p.stat().st_mtime)
    while len(files) > MAX_SESSIONS:
        files.pop(0).unlink()


def list_sessions() -> list[dict[str, Any]]:
    """列出所有历史会话（按时间倒序）。返回轻量摘要列表。"""
    if not DEFAULT_DIR.exists():
        return []
    result = []
    for f in sorted(DEFAULT_DIR.glob("session_*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result.append({
                "session_id": data.get("session_id", f.stem),
                "timestamp": data.get("timestamp", ""),
                "message_count": data.get("message_count", 0),
                "preview": data.get("preview", ""),
            })
        except (json.JSONDecodeError, OSError):
            pass
    return result


def load_session(session_id: str | None = None) -> dict[str, Any] | None:
    """加载指定会话。session_id 为 None 时加载最近一次（last.json）。"""
    if session_id:
        target = DEFAULT_DIR / f"{session_id}.json"
    else:
        target = DEFAULT_DIR / "last.json"
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def export_markdown(messages: list[dict[str, Any]], path: Path) -> Path:
    """导出对话为可读 Markdown 文件。"""
    lines = [
        "# mini-OpenClaw 会话记录",
        "",
        f"> 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 消息总数：{len(messages)}",
        "",
        "---",
        "",
    ]
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content", "")

        if role == "system":
            lines.append("<details>")
            lines.append("<summary>System Prompt</summary>")
            lines.append("")
            lines.append(content)
            lines.append("")
            lines.append("</details>")
            lines.append("")
        elif role == "user":
            lines.append("## 👤 User")
            lines.append("")
            lines.append(content)
            lines.append("")
        elif role == "assistant":
            lines.append("## 🤖 Assistant")
            lines.append("")
            if content:
                lines.append(content)
                lines.append("")
            tool_calls = m.get("tool_calls", [])
            if tool_calls:
                lines.append(f"**调用了 {len(tool_calls)} 个工具：**")
                for tc in tool_calls:
                    args = tc.get("arguments", {})
                    args_str = json.dumps(args, ensure_ascii=False)
                    lines.append(f"- `{tc['name']}({args_str})`")
                lines.append("")
        elif role == "tool":
            tool_name = m.get("name", "?")
            lines.append(f"### 🔧 工具结果：`{tool_name}`")
            lines.append("")
            lines.append("```text")
            lines.append(content[:2000])
            if len(content) > 2000:
                lines.append(f"...（共 {len(content)} 字符，已截断）")
            lines.append("```")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
