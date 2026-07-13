"""Agent 权限分层（Day10）。

三级权限模型（讲义 §4.1）—— 破坏性越高，门槛越高：
  第①层 只读 read / grep / glob      → 自动放行
  第②层 写   write / edit            → 限工作目录内，越界直接拒绝
  第③层 执行 / 外传 bash / web_fetch → 需确认（沙箱在后续步骤）
"""
from __future__ import annotations
from pathlib import Path

READONLY = {"read", "grep", "glob"}
WRITE    = {"write", "edit"}
EXEC     = {"bash", "web_fetch"}

# 破坏性命令模式 —— 即使 --auto-approve 也必须拒绝
# 这些操作不可逆，用户应手动在终端执行
DESTRUCTIVE = (
    "rm -rf",
    "rm -r",
    ":(){",
    "mkfs",
    "dd if=",
    "> /dev/sd",
    "chmod -R",
    "chown -R",
)


def check(tool_name: str, args: dict, workdir: Path) -> str:
    """返回 'allow' / 'confirm' / 'deny'。

    Args:
        tool_name: 工具名（如 "write"、"bash"）
        args: 工具参数字典（如 {"path": "/etc/evil.txt", "content": "..."}）
        workdir: 允许的工作目录（绝对路径），写入操作不得越界

    Returns:
        'allow'   — 安全，直接放行
        'confirm' — 需用户确认
        'deny'    — 越界写入或危险操作，直接拒绝
    """
    # 第①层：只读 → 自动放行
    if tool_name in READONLY:
        return "allow"

    # 第②层：写 → 检查路径是否在工作目录内
    if tool_name in WRITE:
        path_str = args.get("path", "")
        if not path_str:
            return "deny"          # 无路径参数，拒绝
        p = Path(path_str).resolve()
        if str(p).startswith(str(workdir.resolve())):
            return "confirm"       # 工作目录内，需确认
        return "deny"              # 越界，直接拒绝

    # 第③层：执行 / 外传 → 先查破坏性命令，再确认
    if tool_name in EXEC:
        if tool_name == "bash":
            command = args.get("command", "")
            for pattern in DESTRUCTIVE:
                if pattern in command:
                    return "deny"    # 破坏性命令：即使 --auto-approve 也不放行
        return "confirm"

    # 未知工具（含 MCP 工具）：保守策略，先确认
    return "confirm"
