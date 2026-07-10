"""Code Planner Git 工具集：clone、bisect、blame、commit 分析。

Day6+ v1 起可用。所有工具通过 subprocess 调用 git CLI，返回结构化文本。
  Tool.run(**arguments) -> str（observation 文本）
"""
from __future__ import annotations
import os
import subprocess
from pathlib import Path

from .base import Tool


# ---------------------------------------------------------------------------
# 通用工具函数
# ---------------------------------------------------------------------------

def _git(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    """在指定目录运行 git 命令，返回 CompletedProcess。"""
    return subprocess.run(
        ["git"] + cmd, capture_output=True, text=True, timeout=60, cwd=cwd,
    )


def _check_git_repo(repo_path: str) -> str | None:
    """验证路径是否为 git 仓库，不是则返回错误字符串。"""
    repo = Path(repo_path).resolve()
    if not repo.exists():
        return f"错误：路径不存在 —— {repo_path}"
    if not (repo / ".git").exists():
        return f"错误：不是 git 仓库 —— {repo_path}（缺少 .git 目录）"
    return None


# ---------------------------------------------------------------------------
# 1. git_clone
# ---------------------------------------------------------------------------

def _git_clone(url: str, branch: str = "", target_dir: str = "") -> str:
    """克隆远程仓库到本地。"""
    # 从 URL 推导默认目录名
    if not target_dir:
        target_dir = url.rstrip("/").split("/")[-1].removesuffix(".git")

    target_path = Path(target_dir).resolve()
    if target_path.exists():
        return f"错误：目标目录已存在 —— {target_dir}（请选择其他目录或先删除）"

    cmd = ["git", "clone"]
    if branch:
        cmd += ["-b", branch]
    cmd += [url, str(target_path)]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            return f"克隆失败：\n{proc.stderr.strip()}"
        output = proc.stderr.strip() or proc.stdout.strip()  # git clone 输出到 stderr
        return f"""== 克隆成功 ==
仓库: {url}
分支: {branch or '(默认)'}
本地路径: {target_path.resolve()}

--- git clone 输出 ---
{output}"""
    except subprocess.TimeoutExpired:
        return "克隆超时（>120s）。仓库可能过大或网络问题。"
    except FileNotFoundError:
        return "错误：系统中未找到 git。请安装 git。"


git_clone_tool = Tool(
    name="git_clone",
    description="从 GitHub/Git 远程 URL 克隆仓库到本地，支持指定分支。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "远程仓库 URL（如 https://github.com/user/repo.git）"},
            "branch": {"type": "string", "description": "分支名称，默认使用远程默认分支"},
            "target_dir": {"type": "string", "description": "本地目标目录名，默认从 URL 推导"},
        },
        "required": ["url"],
    },
    run=_git_clone,
)


# ---------------------------------------------------------------------------
# 2. git_bisect_start
# ---------------------------------------------------------------------------

def _git_bisect_start(repo_path: str, bad_commit: str = "HEAD", good_commit: str = "") -> str:
    """启动 git bisect 二分查找会话。"""
    err = _check_git_repo(repo_path)
    if err:
        return err
    if not good_commit:
        return "错误：必须提供 good_commit（最后一个确认正常的 commit）"

    try:
        # 先重置可能遗留的 bisect 状态
        _git(["bisect", "reset"], cwd=repo_path)

        # 启动 bisect
        proc = _git(["bisect", "start"], cwd=repo_path)
        if proc.returncode != 0:
            return f"bisect start 失败：\n{proc.stderr.strip()}"

        # 标记 bad
        proc = _git(["bisect", "bad", bad_commit], cwd=repo_path)
        if proc.returncode != 0:
            _git(["bisect", "reset"], cwd=repo_path)
            return f"标记 bad ({bad_commit}) 失败：\n{proc.stderr.strip()}"

        # 标记 good
        proc = _git(["bisect", "good", good_commit], cwd=repo_path)
        if proc.returncode != 0:
            _git(["bisect", "reset"], cwd=repo_path)
            return f"标记 good ({good_commit}) 失败：\n{proc.stderr.strip()}"

        # 获取 bisect 日志，了解当前状态
        log = _git(["bisect", "log"], cwd=repo_path)

        # 获取当前 HEAD
        head = _git(["rev-parse", "--short", "HEAD"], cwd=repo_path)
        current_sha = head.stdout.strip()

        # 获取当前 commit 信息
        show = _git(["log", "--oneline", "-1"], cwd=repo_path)

        remaining_raw = log.stdout
        # 估算剩余步数
        import re
        steps_match = re.findall(r"#\s+\w+:\s+\[([a-f0-9]+)\]", remaining_raw)
        remaining_steps = max(0, len(steps_match) // 2) if steps_match else "?"

        return f"""== Git Bisect 已启动 ==
仓库: {repo_path}
Bad: {bad_commit}
Good: {good_commit}

当前待测试 commit: {current_sha}
  {show.stdout.strip()}

估算剩余步数: ~{remaining_steps}

--- 下一步 ---
请验证当前 commit 是否包含 bug，然后调用 git_bisect_step 标记：
  如果 bug 存在 → verdict="bad"
  如果 bug 不存在 → verdict="good"

--- Bisect Log ---
{log.stdout.strip()[:2000]}"""
    except Exception as e:
        return f"bisect start 异常：{e}"


git_bisect_start_tool = Tool(
    name="git_bisect_start",
    description="启动 git bisect 二分查找：标记 good 和 bad commit，返回当前待测试的 commit SHA。",
    parameters={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string", "description": "仓库本地路径"},
            "bad_commit": {"type": "string", "description": "存在 bug 的 commit，默认 HEAD"},
            "good_commit": {"type": "string", "description": "最后一个确认正常的 commit"},
        },
        "required": ["repo_path", "good_commit"],
    },
    run=_git_bisect_start,
)


# ---------------------------------------------------------------------------
# 3. git_bisect_step
# ---------------------------------------------------------------------------

def _git_bisect_step(repo_path: str, verdict: str) -> str:
    """在 bisect 流程中标记当前 commit。"""
    err = _check_git_repo(repo_path)
    if err:
        return err

    # 检查是否在 bisect 中
    log_proc = _git(["bisect", "log"], cwd=repo_path)
    if log_proc.returncode != 0 or not log_proc.stdout.strip():
        return "错误：当前不在 bisect 会话中。请先调用 git_bisect_start。"

    try:
        proc = _git(["bisect", verdict], cwd=repo_path)
        output = proc.stdout.strip()

        # 检查是否已完成
        if "is the first bad commit" in output:
            # 提取第一个 bad commit
            first_bad_line = [l for l in output.split("\n") if "is the first bad commit" in l]
            commit_sha = first_bad_line[0].split()[0] if first_bad_line else "?"
            show = _git(["show", "--stat", commit_sha], cwd=repo_path)

            # 获取 log 中所有 commits 范围
            range_info = _git(["log", "--oneline", f"{commit_sha}~3..{commit_sha}"], cwd=repo_path)

            return f"""== 🎯 Bisect 完成！引入 Bug 的 Commit ==
Bug 首次出现在: {commit_sha}

--- 改动文件 ---
{show.stdout.strip()[:2000]}

--- 邻近 commits ---
{range_info.stdout.strip()}

--- 下一步 ---
1. 调用 git_show_commit(repo_path="{repo_path}", commit_hash="{commit_sha}") 查看完整 diff
2. 调用 code_analyze 分析该 commit 引入的改动
3. 调用 generate_diff 生成修复建议
4. 调用 git_bisect_reset 结束 bisect 会话"""

        elif verdict == "skip":
            # 解析 log 看还剩多少
            log = _git(["bisect", "log"], cwd=repo_path)
            head = _git(["rev-parse", "--short", "HEAD"], cwd=repo_path)
            show = _git(["log", "--oneline", "-1"], cwd=repo_path)
            return f"""== Bisect 已跳过 ==
当前 commit 已跳过。

下一个待测试: {head.stdout.strip()}
  {show.stdout.strip()}

继续用 git_bisect_step 标记 good/bad/skip。
--- Log ---
{log.stdout.strip()[:2000]}"""
        else:
            # 常规步骤：显示下一个待测试 commit
            head = _git(["rev-parse", "--short", "HEAD"], cwd=repo_path)
            show = _git(["log", "--oneline", "-1"], cwd=repo_path)
            log = _git(["bisect", "log"], cwd=repo_path)

            return f"""== Bisect: 已标记为 {verdict} ==
当前 HEAD: {head.stdout.strip()}
  {show.stdout.strip()}

继续验证并标记，直到找到第一个 bad commit。

--- Bisect Log ---
{log.stdout.strip()[:2000]}"""

    except Exception as e:
        return f"bisect step 异常：{e}"


git_bisect_step_tool = Tool(
    name="git_bisect_step",
    description="在 git bisect 流程中标记当前 commit 为 good/bad/skip，推进二分查找。如 bisect 完成则返回引入 bug 的 commit SHA。",
    parameters={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string", "description": "仓库本地路径"},
            "verdict": {
                "type": "string",
                "enum": ["good", "bad", "skip"],
                "description": "对当前 commit 的判定：good=无bug, bad=有bug, skip=跳过",
            },
        },
        "required": ["repo_path", "verdict"],
    },
    run=_git_bisect_step,
)


# ---------------------------------------------------------------------------
# 4. git_bisect_reset
# ---------------------------------------------------------------------------

def _git_bisect_reset(repo_path: str) -> str:
    """结束 bisect 会话，恢复到原始 HEAD。"""
    err = _check_git_repo(repo_path)
    if err:
        return err

    try:
        proc = _git(["bisect", "reset"], cwd=repo_path)
        if proc.returncode != 0:
            return f"bisect reset 失败：\n{proc.stderr.strip()}"
        return f"✅ Bisect 会话已结束，仓库已恢复到原始状态。\n{proc.stdout.strip() or ''}"
    except Exception as e:
        return f"bisect reset 异常：{e}"


git_bisect_reset_tool = Tool(
    name="git_bisect_reset",
    description="结束 git bisect 会话，恢复到原始 HEAD。",
    parameters={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string", "description": "仓库本地路径"},
        },
        "required": ["repo_path"],
    },
    run=_git_bisect_reset,
)


# ---------------------------------------------------------------------------
# 5. git_blame
# ---------------------------------------------------------------------------

def _git_blame(repo_path: str, file_path: str,
               line_start: int = 0, line_end: int = 0) -> str:
    """逐行显示每个修改的作者和 commit。"""
    err = _check_git_repo(repo_path)
    if err:
        return err

    full_path = Path(repo_path) / file_path
    if not full_path.exists():
        return f"错误：文件不存在 —— {file_path}（在仓库 {repo_path} 中）"

    # 构建 blame 命令
    cmd = ["blame", "--line-porcelain"]
    if line_start > 0 and line_end > 0:
        cmd += ["-L", f"{line_start},{line_end}"]
    elif line_start > 0:
        cmd += ["-L", f"{line_start},"]
    cmd.append(str(full_path))

    try:
        proc = _git(cmd, cwd=repo_path)
        if proc.returncode != 0:
            return f"blame 失败：\n{proc.stderr.strip()}"

        # 解析 --line-porcelain 输出
        output = proc.stdout.strip()
        if not output:
            return f"(文件为空或无可 blame 的内容)"

        # 格式化输出
        lines: list[str] = []
        lines.append(f"== Git Blame: {file_path} ==")
        if line_start:
            lines.append(f"行范围: {line_start}-{line_end or 'EOF'}")
        lines.append("")

        # 简洁格式：按行解析 porcelain 输出
        current_line = ""
        current_commit = ""
        current_author = ""
        current_time = ""
        current_summary = ""

        for raw_line in output.split("\n"):
            if raw_line.startswith("\t"):
                # 这是实际源码行
                source_line = raw_line[1:]
                lines.append(
                    f"L{current_line:4s} | {current_commit[:8]} | {current_author:<15s} | {source_line[:100]}"
                )
            else:
                parts = raw_line.split(" ", 1)
                if len(parts) < 2:
                    continue
                key, value = parts
                if key.isdigit():
                    current_line = key
                elif key == "author":
                    current_author = value
                elif key == "author-time":
                    from datetime import datetime
                    current_time = datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d")
                elif key == "summary":
                    current_summary = value

        return "\n".join(lines[:200])  # 限制行数
    except Exception as e:
        return f"blame 异常：{e}"


git_blame_tool = Tool(
    name="git_blame",
    description="显示文件每行的最后修改 commit 和作者，用于追溯代码变更历史。",
    parameters={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string", "description": "仓库本地路径"},
            "file_path": {"type": "string", "description": "文件路径（相对于仓库根目录）"},
            "line_start": {"type": "integer", "description": "起始行号（可选，默认从开头）"},
            "line_end": {"type": "integer", "description": "结束行号（可选，默认到结尾）"},
        },
        "required": ["repo_path", "file_path"],
    },
    run=_git_blame,
)


# ---------------------------------------------------------------------------
# 6. git_show_commit
# ---------------------------------------------------------------------------

def _git_show_commit(repo_path: str, commit_hash: str) -> str:
    """展示指定 commit 的完整 diff 和元数据。"""
    err = _check_git_repo(repo_path)
    if err:
        return err

    try:
        # 获取 commit 元数据
        show = _git([
            "show", "--stat", "--format=fuller", commit_hash,
        ], cwd=repo_path)
        if show.returncode != 0:
            return f"show commit 失败：\n{show.stderr.strip()}"

        # 获取 diff
        diff = _git([
            "diff", f"{commit_hash}~1", commit_hash,
        ], cwd=repo_path)

        # 统计改动量
        files_changed = show.stdout.split("\n")
        stat_lines = [l for l in files_changed if "changed" in l and "insertion" in l]

        # 尝试获取分支信息
        branches = _git([
            "branch", "--contains", commit_hash,
        ], cwd=repo_path)

        result = f"""== Commit 详情: {commit_hash} ==

--- 元数据 ---
"""
        # 提取 metadata 行
        metadata_lines: list[str] = []
        for line in show.stdout.split("\n"):
            if line.startswith("commit ") or line.startswith("Author") or line.startswith("Commit") or line.startswith("Date"):
                metadata_lines.append(line)
            elif stat_lines and any(s in line for s in stat_lines):
                metadata_lines.append(line)
        result += "\n".join(metadata_lines[:20])

        if branches.returncode == 0 and branches.stdout.strip():
            result += f"\n包含此 commit 的分支: {branches.stdout.strip()}"

        result += f"""

--- Diff ({commit_hash}~1..{commit_hash}) ---
{diff.stdout.strip()[:4000]}
"""
        if len(diff.stdout.strip()) > 4000:
            result += "\n...(diff 已截断，共 {} 字符)".format(len(diff.stdout.strip()))

        return result
    except Exception as e:
        return f"show commit 异常：{e}"


git_show_commit_tool = Tool(
    name="git_show_commit",
    description="展示指定 commit 的完整 diff、作者、时间、消息，用于分析引入 bug 的改动。",
    parameters={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string", "description": "仓库本地路径"},
            "commit_hash": {"type": "string", "description": "Commit SHA（完整或短格式）"},
        },
        "required": ["repo_path", "commit_hash"],
    },
    run=_git_show_commit,
)
