"""文件读写工具（Day5）。

read 带行号输出，便于后续 edit 工具定位替换位置；输出包 <external> 边界（Day10 注入防护）。
write 覆盖写入，自动创建父目录；越界写入由权限层（agent/permissions.py）拦截。
"""
from __future__ import annotations
import os
from .base import Tool
from .guard import wrap_external


def _read(path: str, max_bytes: int = 100_000) -> str:
    """读取文本文件，每行前加行号。超长时截断并提示。

    外部内容包 <external> 边界，告诉模型这是数据而非指令（Day10 注入防护）。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read(max_bytes + 1)
    except FileNotFoundError:
        return f"错误：文件不存在 —— {path}"
    except (PermissionError, OSError) as e:
        return f"错误：无法读取 {path} —— {e}"

    truncated = len(text) > max_bytes
    if truncated:
        text = text[:max_bytes]
    lines = text.splitlines()
    body = "\n".join(f"{i:>6}\t{ln}" for i, ln in enumerate(lines, 1))
    if truncated:
        body += f"\n... [已截断，仅显示前 {max_bytes} 字节]"
    if not body:
        body = "[空文件]"
    # 注入防护：外部内容包边界
    return wrap_external(body, path)


def _write(path: str, content: str) -> str:
    """覆盖写入文件，自动创建父目录。返回含字节数与路径的成功提示。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        n = f.write(content)
    return f"已写入 {n} 字节到 {path}"


read_tool = Tool(
    name="read",
    description="读取指定路径的文本文件内容。",
    parameters={"type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径"}},
                "required": ["path"]},
    run=_read,
)

write_tool = Tool(
    name="write",
    description="把内容写入指定路径（覆盖）。",
    parameters={"type": "object",
                "properties": {"path": {"type": "string"},
                               "content": {"type": "string"}},
                "required": ["path", "content"]},
    run=_write,
)
