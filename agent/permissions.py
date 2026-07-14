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

# bash 安全命令（纯只读，不写文件不执行代码）→ 自动放行
SAFE_BASH = {
    # 文件列表与信息
    "ls", "tree", "dir", "stat", "file", "readlink", "realpath",
    "basename", "dirname",
    # 文本输出
    "cat", "echo", "head", "tail", "nl", "od", "strings",
    # 文本搜索
    "find", "locate", "grep", "rg",
    # 文本处理（只读，无 -i 等原地修改标志）
    "sort", "uniq", "cut", "tr", "fmt", "wc",
    # 比较
    "diff", "cmp", "comm", "sdiff",
    # 校验
    "md5sum", "sha1sum", "sha256sum", "sha512sum", "cksum",
    # 系统信息
    "pwd", "whoami", "date", "which", "type", "command",
    "env", "printenv", "uname", "hostname", "id",
    "df", "du", "free", "uptime", "ps", "pgrep",
    "dmesg", "lsblk", "lscpu", "lsusb", "lspci",
    # 帮助
    "man", "info", "apropos", "whatis",
}
# 含这些运算符的命令即使以安全命令开头也需确认
RISKY_OPERATORS = (">", ">>", "|", ";", "&&", "||", "`", "$(")

# find 虽然是只读工具，但 -delete / -exec / -ok 可以执行破坏操作
RISKY_FIND_FLAGS = ("-delete", "-exec", "-execdir", "-ok", "-okdir")

# 破坏性命令模式 —— 即使 --auto-approve 也必须拒绝
# 仅限于不可逆的系统级破坏操作：fork 炸弹、格式化、裸磁盘写入
DESTRUCTIVE = (
    ":(){",
    "mkfs",
    "dd if=",
    "> /dev/sd",
)

# rm / chmod / chown 只有在目标为系统路径时才 deny，否则走 confirm
DESTRUCTIVE_TARGETS = (
    " / ",     # rm -rf /  、chmod -R /  等
    " /*",     # rm -rf /*
    " ~",      # rm -rf ~
    "/etc", "/usr", "/bin", "/var", "/sys", "/proc",
    "/dev", "/boot", "/root", "/lib", "/sbin", "/opt",
    "$HOME",
)

DESTRUCTIVE_RISKY_CMDS = ("rm", "chmod", "chown")


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
            command = args.get("command", "").strip()
            # 3a. 无条件破坏性命令：fork 炸弹、格式化、裸磁盘写 → 直接 deny
            for pattern in DESTRUCTIVE:
                if pattern in command:
                    return "deny"
            # 3b. rm/chmod/chown 只有目标为系统路径（/、/etc、~ 等）才 deny
            first_word = command.split(maxsplit=1)[0] if command else ""
            if first_word in DESTRUCTIVE_RISKY_CMDS:
                for target in DESTRUCTIVE_TARGETS:
                    if target in command:
                        return "deny"
                # 末尾 " /"（如 rm -rf / 、chmod 777 /）也 deny
                if command.endswith(" /"):
                    return "deny"
            # 3c. 安全纯读命令（ls/cat/echo/…）：自动放行
            if (first_word in SAFE_BASH
                    and not any(op in command for op in RISKY_OPERATORS)):
                # find 额外检查：含 -delete / -exec 等破坏性 flag 则降级为 confirm
                if first_word == "find" and any(f in command for f in RISKY_FIND_FLAGS):
                    return "confirm"
                return "allow"
        return "confirm"

    # 未知工具（含 MCP 工具）：保守策略，先确认
    return "confirm"
