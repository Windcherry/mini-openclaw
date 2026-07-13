"""评测任务集与指标（Day4 体验 / Day7 评测；Day10 任务成功率 / 消融）。

两类评测：
  A) 工具调用质量：在固定测试集上算三项指标（Day4 用 API 体验，Day7 系统化）。
  B) 端到端任务成功率（Day7 起 / Day10 消融）：跑一批任务，看完成率，对比不同配置。

Day3 新增：
  Trajectory-based Task —— 每条任务带一个程序化 check 函数，吃轨迹记录，吐成败布尔值。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable


# ============================================================================
# Day3: Trajectory-based Task（程序化判据）
# ============================================================================

# 一条"轨迹记录"长这样：
#   {"task": "任务名",
#    "steps": [ {tool_calls, raw, prompt_tokens, completion_tokens}, ... ],
#    "final": "agent 的最终自然语言答复"}
Trajectory = dict[str, Any]


@dataclass
class Task:
    name: str
    instruction: str                       # 给 agent 的指令
    check: Callable[[Trajectory], bool]    # 成功判据：吃一条轨迹，判成败


# ---- 辅助函数 ----

def _any_tool_call(traj: Trajectory, name: str) -> bool:
    """轨迹中是否出现过某个工具调用。"""
    return any(
        tc.get("name") == name
        for s in traj.get("steps", [])
        for tc in s.get("tool_calls", [])
    )


def _any_tool_call_with(traj: Trajectory, name: str, kw: str) -> bool:
    """轨迹中是否调用了某工具，且其 arguments 里含有指定子串。"""
    return any(
        tc.get("name") == name and kw in str(tc.get("arguments", {}))
        for s in traj.get("steps", [])
        for tc in s.get("tool_calls", [])
    )


def _final_has(traj: Trajectory, *keywords: str) -> bool:
    """最终答复中是否包含任一关键词。"""
    final = traj.get("final", "")
    return any(kw in final for kw in keywords)


# ---- 成功判据（程序化优先）----

# 目标1：全面盘点现状与摸清骨架
# ----------------------------------------

def _check_read_config(traj: Trajectory) -> bool:
    """成功 = 期间调用过 read 且最终答复里报出了 timeout 的值。"""
    return _any_tool_call(traj, "read") and "30" in traj.get("final", "")


def _check_list_dir(traj: Trajectory) -> bool:
    """成功 = 期间调用过 bash 且命令里含 ls。"""
    return _any_tool_call_with(traj, "bash", "ls")


def _check_scan_files(traj: Trajectory) -> bool:
    """目标1-扫描文件：用 glob 找出所有 .py 文件，答复列出文件清单。"""
    return _any_tool_call(traj, "glob") and _final_has(traj, ".py")


def _check_inspect_file(traj: Trajectory) -> bool:
    """目标1-推断文件用途：调用 read 读取文件，答复推断该文件的业务功能。"""
    used_read = _any_tool_call(traj, "read")
    return used_read and _final_has(traj, "功能", "作用", "用途", "模块", "工具")


# 目标2：依赖与外部知识检索
# ----------------------------------------

def _check_dependencies(traj: Trajectory) -> bool:
    """目标2-读依赖：调用 read 读取 requirements.txt，答复列出关键依赖名。"""
    used_read = _any_tool_call_with(traj, "read", "requirements")
    return used_read and _final_has(traj, "httpx", "pydantic", "markdownify", "pylint", "radon")


def _check_research_lib(traj: Trajectory) -> bool:
    """目标2-查生僻库：调用了 web_fetch，答复含该库的用途说明。"""
    return _any_tool_call(traj, "web_fetch") and _final_has(traj, "库", "框架", "工具", "用于", "作用")


# 目标3：定位核心模块与动态验证
# ----------------------------------------

def _check_locate_entry(traj: Trajectory) -> bool:
    """目标3-核心定位：用 grep 搜索入口点（def main / if __name__），答复指出文件路径。"""
    used_grep = _any_tool_call(traj, "grep")
    return used_grep and _final_has(traj, ".py", "main", "__name__")


def _check_run_selfcheck(traj: Trajectory) -> bool:
    """目标3-动态验证：用 bash 运行 --selfcheck，答复判断是否通过。"""
    used_bash = _any_tool_call_with(traj, "bash", "selfcheck")
    return used_bash and _final_has(traj, "通过", "ok", "成功", "✅", "失败", "❌")


def _check_find_todos(traj: Trajectory) -> bool:
    """目标3-搜 TODO：用 grep/code_search 搜索 TODO，答复列出数量或位置。"""
    used_search = any(
        _any_tool_call(traj, t) for t in ("grep", "code_search")
    )
    return used_search and _final_has(traj, "TODO", "todo")


# 目标4：重排布与重命名
# ----------------------------------------

def _check_refactor_plan(traj: Trajectory) -> bool:
    """目标4-重构方案：调用了 write 生成重构脚本或用 bash 创建目录。
    开放式——判据验证有"规划 + 动手"，报告质量用 LLM-as-judge。
    """
    has_plan = (
        _any_tool_call(traj, "write")
        or _any_tool_call_with(traj, "bash", "mkdir")
    )
    return has_plan and _final_has(traj, "重构", "移动", "重命名", "目录", "整理", "mv")


def _check_refactor_execute(traj: Trajectory) -> bool:
    """目标4-执行重构：用 bash 执行了 mv / mkdir 等文件操作。"""
    return _any_tool_call_with(traj, "bash", "mv") or _any_tool_call_with(traj, "bash", "mkdir")


# 目标5：生成全面的 README 并输出报告
# ----------------------------------------

def _check_generate_readme(traj: Trajectory) -> bool:
    """目标5-生成 README：调用了 write 写入 README.md，答复提及生成/写入成功。"""
    used_write = _any_tool_call_with(traj, "write", "README")
    return used_write and _final_has(traj, "README", "生成", "写入", "创建", "报告")


def _check_output_report(traj: Trajectory) -> bool:
    """目标5-输出报告：最终答复包含结构化的模块导读（含模块名、架构描述）。
    LLM-as-judge 会给报告的完整度打分；这里只做最低限检查。
    """
    return _final_has(traj, "模块", "架构", "层", "目录") and len(traj.get("final", "")) > 200


# ---- 任务集 ----

SAMPLE_TASKS: list[Task] = [
    # 基础能力
    Task("read-config", "读取 config.json，告诉我 timeout 是多少", _check_read_config),
    Task("list-dir", "列出当前目录下的文件", _check_list_dir),

    # 目标1：盘点现状与摸清骨架
    Task("scan-files",
         "用 glob 找出本项目所有 .py 文件，告诉我它们都在哪些目录下",
         _check_scan_files),
    Task("inspect-file",
         "用 read 读取 tools/base.py，推断这个文件的业务功能是什么",
         _check_inspect_file),

    # 目标2：依赖与外部知识检索
    Task("check-deps",
         "读取 requirements.txt，告诉我这个项目依赖了哪些库",
         _check_dependencies),
    Task("research-lib",
         "用 web_fetch 查一下 pydantic 这个库是干什么的，用一句话总结",
         _check_research_lib),

    # 目标3：定位核心模块与动态验证
    Task("locate-entry",
         "用 grep 搜索本项目里所有 def main 和 if __name__ == '__main__'，找出程序的入口点",
         _check_locate_entry),
    Task("run-selfcheck",
         "用 bash 运行 python -m agent.cli --selfcheck，告诉我检查结果",
         _check_run_selfcheck),
    Task("find-todos",
         "用 grep 搜索本项目所有 TODO 注释，统计有多少个，列出前 5 条",
         _check_find_todos),

    # 目标4：重排布与重命名
    Task("refactor-plan",
         "当前项目有些文件命名混乱（如 eval/ 下有 test1.py、a.txt），请设计一套规范的重构方案",
         _check_refactor_plan),
    Task("refactor-execute",
         "用 bash 的 mkdir -p 和 mv 命令，把 demo_m2.py 移到 examples/ 下（如果没有目录就先创建）",
         _check_refactor_execute),

    # 目标5：生成 README 与输出报告
    Task("generate-readme",
         "分析完项目结构后，用 write 在项目根目录生成一份中文 README.md，说明项目架构和各模块用途",
         _check_generate_readme),
    Task("output-report",
         "综合前面所有的分析结果，给我一份完整、结构清晰的 mini-OpenClaw 模块导读报告",
         _check_output_report),
]


# ============================================================================
# Day6–7: 工具调用固定测试集
# ============================================================================

@dataclass
class ToolCallCase:
    request: str                 # 用户请求
    expected_tool: str           # 期望调用的工具名
    expected_args: dict          # 期望参数（可只校验关键字段）


# Day6 固定测试集（教师会提供 ~50 条；这里给格式示例）
TOOLCALL_TESTSET: list[ToolCallCase] = [
    ToolCallCase("把 a.txt 的内容读出来", "read", {"path": "a.txt"}),
    ToolCallCase("在当前目录运行 ls", "bash", {"command": "ls"}),
    # TODO[Day7] 按你组的领域补充更多用例
]


# ============================================================================
# Day10: 端到端任务集（消融用）
# ============================================================================

@dataclass
class E2ETask:
    name: str
    instruction: str
    check: str                   # 如何判定成功（人工/脚本）


# Day10 端到端任务集（消融用）
E2E_TASKS: list[E2ETask] = [
    E2ETask("hello", "创建 hello.py 并运行，输出当前时间", "存在 hello.py 且运行打印了时间"),
    E2ETask("todo-report", "扫描本项目所有 Python 文件里的 TODO 注释，生成 markdown 报告",
            "生成的报告列出了真实存在的 TODO"),
    # TODO[Day10] 补充你领域的任务
]
