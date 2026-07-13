"""评测指标（Day3 骨架 → Day7 评测 → Day10 消融）。

四项核心指标：
  - success_rate: 任务成功率（对接 eval/tasks.py 的程序化判据）
  - step_count: 单条轨迹的步数
  - token_count: 单条轨迹的总 token 开销
  - json_valid_rate: 模型输出 tool_call JSON 的合法率

Day3：放预录制轨迹样本，实现指标函数，跑通计算管线。
Day7+：样本轨迹被真实 agent 运行记录替换。
"""
from __future__ import annotations
import json
import re
from typing import Any

# 匹配 raw 文本里的 <tool_call>{"name": ..., "arguments": {...}}</tool_call>
TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


# ============================================================================
# 预录制轨迹样本（Day3 手工构造；Day4 起被真轨迹替换）
# ============================================================================

SAMPLE_RECORDS: list[dict[str, Any]] = [
    # --- 基础能力 ---
    {
        "task": "read-config",
        "steps": [
            {"tool_calls": [{"name": "read", "arguments": {"path": "config.json"}}],
             "raw": '<tool_call>{"name": "read", "arguments": {"path": "config.json"}}</tool_call>',
             "prompt_tokens": 120, "completion_tokens": 35},
            {"tool_calls": [],
             "raw": "文件里有 timeout=30，所以我报出 30。",
             "prompt_tokens": 200, "completion_tokens": 18},
        ],
        "final": "config.json 里的 timeout 值是 30 秒。",
    },
    {
        "task": "list-dir",
        "steps": [
            {"tool_calls": [{"name": "bash", "arguments": {"command": "ls -la"}}],
             "raw": '<tool_call>{"name": "bash", "arguments": {"command": "ls -la"}}</tool_call>',
             "prompt_tokens": 110, "completion_tokens": 30},
        ],
        "final": "当前目录下有 agent/  backend/  tools/  eval/  等目录。",
    },

    # --- 目标1：盘点现状与摸清骨架 ---
    {
        "task": "scan-files",
        "steps": [
            {"tool_calls": [{"name": "glob", "arguments": {"pattern": "**/*.py"}}],
             "raw": '<tool_call>{"name": "glob", "arguments": {"pattern": "**/*.py"}}</tool_call>',
             "prompt_tokens": 115, "completion_tokens": 28},
        ],
        "final": "项目共有 26 个 .py 文件，分布在 agent/（5个）、backend/（4个）、tools/（6个）、eval/（3个）、mcp/（4个）等目录。",
    },
    {
        "task": "inspect-file",
        "steps": [
            {"tool_calls": [{"name": "read", "arguments": {"path": "tools/base.py"}}],
             "raw": '<tool_call>{"name": "read", "arguments": {"path": "tools/base.py"}}</tool_call>',
             "prompt_tokens": 130, "completion_tokens": 40},
        ],
        "final": "tools/base.py 定义了 Tool 数据类和 ToolRegistry 注册表，是整个工具系统的核心抽象层，负责工具的注册、查找和 schema 导出。",
    },

    # --- 目标2：依赖与外部知识检索 ---
    {
        "task": "check-deps",
        "steps": [
            {"tool_calls": [{"name": "read", "arguments": {"path": "requirements.txt"}}],
             "raw": '<tool_call>{"name": "read", "arguments": {"path": "requirements.txt"}}</tool_call>',
             "prompt_tokens": 118, "completion_tokens": 32},
        ],
        "final": "项目依赖 httpx、pydantic、markdownify、pylint 和 radon。",
    },
    {
        "task": "research-lib",
        "steps": [
            {"tool_calls": [{"name": "web_fetch", "arguments": {"url": "https://docs.pydantic.dev/latest/"}}],
             "raw": '<tool_call>{"name": "web_fetch", "arguments": {"url": "https://docs.pydantic.dev/latest/"}}</tool_call>',
             "prompt_tokens": 140, "completion_tokens": 45},
        ],
        "final": "pydantic 是一个 Python 数据校验库，用类型注解定义数据模型，自动做运行时校验和序列化。",
    },

    # --- 目标3：定位核心模块与动态验证 ---
    {
        "task": "locate-entry",
        "steps": [
            {"tool_calls": [{"name": "grep", "arguments": {"pattern": "def main|__name__.*__main__", "path": "."}}],
             "raw": '<tool_call>{"name": "grep", "arguments": {"pattern": "def main|__name__.*__main__", "path": "."}}</tool_call>',
             "prompt_tokens": 125, "completion_tokens": 50},
        ],
        "final": "入口点在 agent/cli.py 的 main() 函数，以及 mcp/echo_server.py 的 if __name__ == '__main__' 块。",
    },
    {
        "task": "run-selfcheck",
        "steps": [
            {"tool_calls": [{"name": "bash", "arguments": {"command": "python -m agent.cli --selfcheck"}}],
             "raw": '<tool_call>{"name": "bash", "arguments": {"command": "python -m agent.cli --selfcheck"}}</tool_call>',
             "prompt_tokens": 130, "completion_tokens": 38},
        ],
        "final": "自检通过 ✅，3 项检查全部 ok：工具注册表、FakeBackend、主循环模块。",
    },
    {
        "task": "find-todos",
        "steps": [
            {"tool_calls": [{"name": "grep", "arguments": {"pattern": "TODO", "path": "."}}],
             "raw": '<tool_call>{"name": "grep", "arguments": {"pattern": "TODO", "path": "."}}</tool_call>',
             "prompt_tokens": 120, "completion_tokens": 55},
        ],
        "final": "共发现 33 个 TODO 标记。前 5 条：tools/fs.py:7（实现 read）、tools/shell.py:7（实现 bash）、agent/loop.py:43（工具分发）...",
    },

    # --- 目标4：重排布与重命名 ---
    {
        "task": "refactor-plan",
        "steps": [
            {"tool_calls": [{"name": "write", "arguments": {"path": "refactor_plan.md", "content": "# 重构方案\n\n..."}}],
             "raw": '<tool_call>{"name": "write", "arguments": {"path": "refactor_plan.md", "content": "# 重构方案\\n\\n..."}}</tool_call>',
             "prompt_tokens": 200, "completion_tokens": 80},
        ],
        "final": "已设计重构方案：将 demo_m2.py 移入 examples/，将乱码命名的文件统一为 snake_case，整理 docs/ 下的笔记文件。方案已写入 refactor_plan.md。",
    },
    {
        "task": "refactor-execute",
        "steps": [
            {"tool_calls": [{"name": "bash", "arguments": {"command": "mkdir -p examples && mv demo_m2.py examples/"}}],
             "raw": '<tool_call>{"name": "bash", "arguments": {"command": "mkdir -p examples && mv demo_m2.py examples/"}}</tool_call>',
             "prompt_tokens": 140, "completion_tokens": 42},
        ],
        "final": "已执行：创建 examples/ 目录，将 demo_m2.py 移入。",
    },

    # --- 目标5：生成 README 与输出报告 ---
    {
        "task": "generate-readme",
        "steps": [
            {"tool_calls": [{"name": "repo_structure", "arguments": {"root_path": ".", "max_depth": 3}}],
             "raw": '<tool_call>{"name": "repo_structure", "arguments": {"root_path": ".", "max_depth": 3}}</tool_call>',
             "prompt_tokens": 150, "completion_tokens": 60},
            {"tool_calls": [{"name": "write", "arguments": {"path": "README.md", "content": "# mini-OpenClaw\n\n..."}}],
             "raw": '<tool_call>{"name": "write", "arguments": {"path": "README.md", "content": "# mini-OpenClaw\\n\\n..."}}</tool_call>',
             "prompt_tokens": 300, "completion_tokens": 120},
        ],
        "final": "已生成 README.md，包含项目简介、架构图（4 层）、模块说明表和快速开始指南。",
    },
    {
        "task": "output-report",
        "steps": [
            {"tool_calls": [{"name": "repo_structure", "arguments": {"root_path": ".", "max_depth": 3}}],
             "raw": '<tool_call>{"name": "repo_structure", "arguments": {"root_path": ".", "max_depth": 3}}</tool_call>',
             "prompt_tokens": 160, "completion_tokens": 65},
            {"tool_calls": [{"name": "dep_graph", "arguments": {"root_path": "."}}],
             "raw": '<tool_call>{"name": "dep_graph", "arguments": {"root_path": "."}}</tool_call>',
             "prompt_tokens": 350, "completion_tokens": 80},
        ],
        "final": (
            "## mini-OpenClaw 模块导读报告\n\n"
            "系统采用自底向上的高内聚、低耦合设计，整体划分为四大核心层级：\n\n"
            "### 1. LLM 交互层 (backend/)\n"
            "- `client.py`: DeepSeek API 客户端，负责消息格式转换和 HTTP 通信\n"
            "- `fake_backend.py`: 离线占位后端，规则驱动模拟工具调用\n\n"
            "### 2. 主循环与上下文流控层 (agent/)\n"
            "- `loop.py`: ReAct 主循环，编排推理-行动循环\n"
            "- `context.py`: Token 预算估算和上下文压缩\n"
            "- `prompts.py`: 全局 SYSTEM_PROMPT，定义智能体行为规范\n\n"
            "### 3. 工具执行层 (tools/ & mcp/ & skills/)\n"
            "- `tools/base.py`: Tool 数据类与 ToolRegistry 注册表\n"
            "- `tools/fs.py`: 文件读写工具 (read/write)\n"
            "- `tools/shell.py`: 受限 shell 执行 (bash)\n"
            "- `tools/code_analysis.py`: 8 个代码分析工具\n"
            "- `tools/git_ops.py`: 6 个 Git 操作工具\n"
            "- `mcp/client.py`: MCP 协议客户端，接入外部工具\n"
            "- `skills/loader.py`: SKILL.md 领域知识加载器\n\n"
            "### 4. 评估基座 (eval/)\n"
            "- `tasks.py`: 任务定义与程序化判据\n"
            "- `metrics.py`: 成功率/步数/Token/JSON合法率四维指标\n\n"
            "各层之间通过统一的消息格式（role/content/tool_calls）传递信息，上层不对下层实现细节产生硬依赖。"
        ),
    },

    # --- 故意失败的样本（用于拉低指标，验证判据有效性）---
    {
        "task": "read-config",
        "steps": [
            {"tool_calls": [{"name": "bash", "arguments": {"command": "cat config.json"}}],
             "raw": '<tool_call>{"name": "bash", "arguments": {"command": "cat config.json"}}</tool_call>',
             "prompt_tokens": 120, "completion_tokens": 36},
        ],
        "final": "不知道 timeout 是多少。（没有用 read 工具，自然找不到值）",
    },
    {
        "task": "find-todos",
        "steps": [
            {"tool_calls": [{"name": "grep", "arguments": {"pattern": "TODO", "path": "."}}],
             "raw": '<tool_call>{"name": "grep" "arguments": {"pattern": "TODO" "path": "."}}</tool_call>',
             "prompt_tokens": 110, "completion_tokens": 44},
        ],
        "final": "抱歉，工具调用出错了。（这段 raw 里是坏 JSON——缺逗号，json_valid_rate 会测出来）",
    },
]


# ============================================================================
# 四项指标函数
# ============================================================================

def success_rate(tasks: list, records: list[dict]) -> float:
    """对每条 (task, trajectory) 记录跑 task.check，返回成功比例。"""
    by_name = {t.name: t for t in tasks}
    ok = 0
    for r in records:
        task = by_name.get(r["task"])
        if task and task.check(r):
            ok += 1
    return ok / max(len(records), 1)


def step_count(record: dict) -> int:
    """一条轨迹的步数（= 调了多少次后端）。"""
    return len(record.get("steps", []))


def token_count(record: dict) -> int:
    """一条轨迹的总 token 开销（prompt + completion）。"""
    return sum(
        s.get("prompt_tokens", 0) + s.get("completion_tokens", 0)
        for s in record.get("steps", [])
    )


def json_valid_rate(records: list[dict]) -> float:
    """从每步的 tool_calls（结构化）或 raw 里的 <tool_call>（文本）提取 JSON 并校验。

    遍历所有轨迹的所有步骤：如果 raw 文本里包含 <tool_call>...</tool_call>，
    尝试 json.loads 校验其内容。不包含 tool_call 的步骤（纯文本答复）不计入分母。
    """
    total, ok = 0, 0
    for r in records:
        for s in r.get("steps", []):
            m = TOOL_CALL_RE.search(s.get("raw", ""))
            if not m:
                continue                # 这步没打算调工具，不计入
            total += 1
            try:
                json.loads(m.group(1))
                ok += 1
            except json.JSONDecodeError:
                pass                     # 坏 JSON：计入分母、不计入分子
    return ok / max(total, 1)


# ============================================================================
# 保留：Day6–7 工具调用质量指标（工具选择正确率 / 参数正确率）
# ============================================================================

def tool_choice_accuracy(preds: list[dict], expected_tools: list[str]) -> float:
    """工具选择正确率：预测的工具名与期望是否一致。"""
    correct = sum(1 for p, e in zip(preds, expected_tools) if p.get("name") == e)
    return correct / max(len(expected_tools), 1)


def arg_accuracy(preds: list[dict], expected_args: list[dict]) -> float:
    """关键参数匹配率：期望 args 的每个键值都在预测里对上。"""
    correct = 0
    for p, e in zip(preds, expected_args):
        pa = p.get("arguments", {})
        if all(str(pa.get(k)) == str(v) for k, v in e.items()):
            correct += 1
    return correct / max(len(expected_args), 1)


# ============================================================================
# 工具调用质量指标（Day6-7 保留）
# ============================================================================
# tool_choice_accuracy / arg_accuracy 定义在上方，供外部导入使用。
