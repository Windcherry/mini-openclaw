"""受控 shell 执行（Day5：bash；Day10：加沙箱与权限）。

bash 工具是 Agent 操作文件系统、运行脚本、安装依赖的唯一途径。
失败信息必须"响亮"——stderr 和 returncode 原样回喂，模型才能自我修复。
"""
from __future__ import annotations
import subprocess
from .base import Tool


def _bash(command: str, timeout: int = 30) -> str:
    """在工作目录中执行一条 shell 命令。

    Args:
        command: 要执行的 shell 命令
        timeout: 超时秒数（默认 30s）

    Returns:
        stdout 内容；若有 stderr 或非零 returncode 则追加，让模型感知到失败。
    """
    try:
        p = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"[超时] 命令超过 {timeout}s 未结束：{command}"

    out = p.stdout or ""
    if p.stderr:
        out += f"\n[stderr]\n{p.stderr}"
    if p.returncode != 0:
        out += f"\n[returncode={p.returncode}]"
    return out.strip() or "[无输出]"


bash_tool = Tool(
    name="bash",
    description="在工作目录中执行一条 shell 命令并返回输出。",
    parameters={"type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"]},
    run=_bash,
)
