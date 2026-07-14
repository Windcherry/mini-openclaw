"""受控 shell 执行（Day5：bash；Day10：加沙箱与权限）。

bash 工具是 Agent 操作文件系统、运行脚本、安装依赖的唯一途径。
失败信息必须"响亮"——stderr 和 returncode 原样回喂，模型才能自我修复。

Day10 沙箱（讲义 §2）—— 纵深防御：
  1. bwrap（bubblewrap）：只读根文件系统 + 仅工作目录可写 + 禁网
  2. 无 bwrap 时降级：命令黑名单 + 路径校验兜底
"""
from __future__ import annotations
import os
import shutil
import subprocess
from .base import Tool

# ── bwrap 可用性检测（缓存，避免每次 bash 调用都测一遍）──
_bwrap_ok: bool | None = None


def _check_bwrap() -> bool:
    """检测 bwrap 是否真的能用（不只是存在）。"""
    global _bwrap_ok
    if _bwrap_ok is not None:
        return _bwrap_ok
    if not shutil.which("bwrap"):
        _bwrap_ok = False
        return False
    try:
        r = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "true"],
            capture_output=True, timeout=5,
        )
        _bwrap_ok = r.returncode == 0
    except Exception:
        _bwrap_ok = False
    return _bwrap_ok


# 兜底黑名单 —— bwrap 不可用时的最后防线
DENY_PATTERNS = (
    "rm -rf /",
    ":(){",
    "mkfs",
    "dd if=",
    "> /dev/sd",
    "curl",
    "wget",
)


def _sanitize(s: str) -> str:
    """清理 surrogate 字符，确保 UTF-8 可编码。"""
    if not s:
        return s
    # 先尝试 surrogateescape 回环（处理 subprocess 输出的非法字节）
    # 如果失败（surrogates 非 surrogateescape 来源），用 replace 兜底
    try:
        return s.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
    except UnicodeEncodeError:
        return s.encode("utf-8", errors="replace").decode("utf-8")


def _bash(command: str, timeout: int = 30) -> str:
    """在工作目录中执行一条 shell 命令（受沙箱保护）。

    Args:
        command: 要执行的 shell 命令
        timeout: 超时秒数（默认 30s）

    Returns:
        stdout 内容；若有 stderr 或非零 returncode 则追加，让模型感知到失败。
    """
    # 兜底黑名单：无论是否有 bwrap，高危命令直接拒绝
    for pattern in DENY_PATTERNS:
        if pattern in command:
            return f"[沙箱] 拒绝执行高危命令（匹配黑名单 '{pattern}'）：{command}"

    if _check_bwrap():
        # bwrap 纵深沙箱：
        #   --ro-bind / /      → 根文件系统只读
        #   --bind cwd cwd     → 仅工作目录可写（覆盖根目录的同路径只读绑定）
        #   --unshare-net      → 禁止网络访问
        #   --dev /dev         → 提供最小 /dev
        cwd = os.getcwd()
        cmd = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--bind", cwd, cwd,
            "--unshare-net",
            "--dev", "/dev",
            "bash", "-c", command,
        ]
    else:
        # 无 bwrap：降级为黑名单兜底（已在上面检查）
        cmd = ["bash", "-c", command]

    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="surrogateescape")
    except subprocess.TimeoutExpired:
        return f"[超时] 命令超过 {timeout}s 未结束：{command}"

    # 清理 surrogate 字符（subprocess 在 text=True + surrogateescape 下可能产生）
    out = p.stdout or ""
    out = _sanitize(out)
    if p.stderr:
        out += f"\n[stderr]\n{_sanitize(p.stderr)}"
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
