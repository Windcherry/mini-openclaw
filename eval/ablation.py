"""消融实验：固定任务集，只改一个因素，对比两组样本轨迹。

D4 起用真轨迹替换这些构造样本，每组多次运行取均值再下结论。
这是 D4（量化上下文/压缩策略）的预演，也是最终 Demo 展示要用到的方法学。

消融维度：
  1. system-prompt 有/无     —— 验证"告诉 agent 工具约定"是否必要
  2. compaction 有/无（Day7）—— 验证上下文压缩对长任务的必要性
  3. truncation 有/无（Day7）—— 验证 observation 截断对 token 控制的作用
  4. MCP 工具有/无（Day10） —— 验证 MCP 协议集成对扩展能力的影响
  5. Skill 加载有/无（Day10）—— 验证领域知识注入对任务质量的提升

使用方式：
  python -m eval.ablation          # 运行全部 5 项消融并打印报告
  python -m eval.ablation --name compaction  # 只跑指定消融
"""
from __future__ import annotations
from typing import Any

from eval.tasks import SAMPLE_TASKS, ABLATION_TASKS_DAY7, ABLATION_TASKS_DAY10
from eval.metrics import success_rate, token_count, step_count


# ============================================================================
# 消融 1：有 / 无 system-prompt
# ============================================================================

# A 组：带 system-prompt（agent 被告知"需要时用 <tool_call> 调工具"）——都成功
GROUP_WITH_SYS: list[dict[str, Any]] = [
    {"task": "read-config",
     "steps": [{"tool_calls": [{"name": "read", "arguments": {"path": "config.json"}}],
                "raw": '<tool_call>{"name":"read","arguments":{"path":"config.json"}}</tool_call>',
                "prompt_tokens": 330, "completion_tokens": 22}],
     "final": "config.json 里 timeout = 30 秒。"},
    {"task": "list-dir",
     "steps": [{"tool_calls": [{"name": "bash", "arguments": {"command": "ls"}}],
                "raw": '<tool_call>{"name":"bash","arguments":{"command":"ls"}}</tool_call>',
                "prompt_tokens": 300, "completion_tokens": 18}],
     "final": "当前目录有：main.py config.json README.md"},
]

# B 组：无 system-prompt（agent 不知道工具约定，直接瞎答）——都失败
GROUP_NO_SYS: list[dict[str, Any]] = [
    {"task": "read-config",
     "steps": [{"tool_calls": [], "raw": "timeout 应该是个常见的默认值。",
                "prompt_tokens": 120, "completion_tokens": 14}],
     "final": "timeout 应该是个常见的默认值。"},
    {"task": "list-dir",
     "steps": [{"tool_calls": [], "raw": "你可以自己用 ls 看看。",
                "prompt_tokens": 110, "completion_tokens": 12}],
     "final": "你可以自己用 ls 看看。"},
]


# ============================================================================
# 消融工具函数
# ============================================================================

def summarize(name: str, recs: list[dict],
              tasks: list = None) -> dict[str, Any]:
    """计算一组轨迹的核心指标并打印。

    Returns:
        {"name": str, "success_rate": float, "avg_tokens": float,
         "avg_steps": float, "n": int}
    """
    if tasks is None:
        tasks = SAMPLE_TASKS
    sr = success_rate(tasks, recs)
    avg_tok = sum(token_count(r) for r in recs) / max(len(recs), 1)
    avg_step = sum(step_count(r) for r in recs) / max(len(recs), 1)
    print(f"  {name:<20s}  成功率={sr:.2f}  平均token={avg_tok:.0f}  "
          f"平均步数={avg_step:.1f}  (n={len(recs)})")
    return {
        "name": name,
        "success_rate": sr,
        "avg_tokens": avg_tok,
        "avg_steps": avg_step,
        "n": len(recs),
    }


def ablation_report(title: str, variable: str, fixed: str,
                    groups: list[dict]) -> None:
    """打印一份消融报告。

    Args:
        title: 消融标题（如 "有/无 system-prompt"）
        variable: 被改变的变量
        fixed: 被固定的因素
        groups: [{"name": ..., "success_rate": ..., ...}, ...]
    """
    print(f"\n{'='*50}")
    print(f"消融：{title}")
    print(f"{'='*50}")
    print(f"  变量: {variable}（其余固定）")
    print(f"  固定: {fixed}")
    for g in groups:
        print(f"  {g['name']}: 成功率={g['success_rate']:.2f}  "
              f"avg_token={g['avg_tokens']:.0f}  avg_steps={g['avg_steps']:.1f}")
    if len(groups) >= 2:
        delta = groups[0]["success_rate"] - groups[1]["success_rate"]
        print(f"  Δ成功率 = {delta:+.2f}")
    print()


# ============================================================================
# 消融 2：有/无 context compaction（Day7）
# ============================================================================

# A 组：有 compaction —— 长对话触发 maybe_compact，历史摘要注入，
# token 可控，agent 不会丢失早期关键信息
GROUP_WITH_COMPACTION: list[dict[str, Any]] = [
    {
        "task": "multi-file-analysis",
        "steps": [
            {"tool_calls": [{"name": "read", "arguments": {"path": "agent/loop.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"agent/loop.py"}}</tool_call>',
             "prompt_tokens": 350, "completion_tokens": 1800},
            {"tool_calls": [{"name": "read", "arguments": {"path": "agent/permissions.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"agent/permissions.py"}}</tool_call>',
             "prompt_tokens": 2400, "completion_tokens": 1200},
            {"tool_calls": [{"name": "read", "arguments": {"path": "tools/shell.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"tools/shell.py"}}</tool_call>',
             "prompt_tokens": 3900, "completion_tokens": 800},
            # ── 此时触发 maybe_compact：前 3 轮历史被摘要，token 预算回落到 ~2000 ──
            {"tool_calls": [{"name": "read", "arguments": {"path": "tools/guard.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"tools/guard.py"}}</tool_call>',
             "prompt_tokens": 2200, "completion_tokens": 600},
            {"tool_calls": [{"name": "read", "arguments": {"path": "tools/fs.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"tools/fs.py"}}</tool_call>',
             "prompt_tokens": 3100, "completion_tokens": 500},
        ],
        "final": (
            "## 模块协作总结\n\n"
            "### agent/loop.py — ReAct 主循环\n"
            "编排推理-行动循环：调用后端 → 解析 tool_calls → permissions.check() → 执行工具 → 注入 observation。\n"
            "集成了 LoopDetector（绕圈/停滞检测）、TodoList 注入、错误分类重试（TransientError vs PermanentError）。\n\n"
            "### agent/permissions.py — 3 级权限模型\n"
            "READONLY 自动放行 / WRITE 工区边界检查 / EXEC 多级递进（SAFE_BASH → RISKY_OPERATORS → DESTRUCTIVE）。\n"
            "deny 不可绕过，confirm 可被 --auto-approve 跳过。\n\n"
            "### tools/shell.py — 受控 shell 执行\n"
            "bwrap 沙箱（ro-bind /、bind cwd、unshare-net）+ DENY_PATTERNS 兜底。\n\n"
            "### tools/guard.py — 注入防护\n"
            "<external> 边界隔离 + 出站域名白名单（ALLOW_HOSTS）。\n\n"
            "### tools/fs.py — 文件读写\n"
            "read 带行号 + <external> 包裹；write 自动 mkdir，越界由 permissions 层拦截。\n\n"
            "**协作链路**：loop 收到模型 tool_call → permissions.check() 裁决 → 工具执行（shell 叠加沙箱、fs 叠加 guard 注入防护）→ observation 回喂模型。"
        ),
    },
]

# B 组：无 compaction —— 上下文线性增长，后面的 tool output 挤掉早期记忆，
# agent 对最早读的文件记忆模糊，最终总结遗漏关键信息
GROUP_NO_COMPACTION: list[dict[str, Any]] = [
    {
        "task": "multi-file-analysis",
        "steps": [
            {"tool_calls": [{"name": "read", "arguments": {"path": "agent/loop.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"agent/loop.py"}}</tool_call>',
             "prompt_tokens": 350, "completion_tokens": 1800},
            {"tool_calls": [{"name": "read", "arguments": {"path": "agent/permissions.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"agent/permissions.py"}}</tool_call>',
             "prompt_tokens": 2400, "completion_tokens": 1200},
            {"tool_calls": [{"name": "read", "arguments": {"path": "tools/shell.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"tools/shell.py"}}</tool_call>',
             "prompt_tokens": 3900, "completion_tokens": 800},
            # 无 compaction：上下文持续膨胀，早期文件内容已被后续输出挤出有效窗口
            {"tool_calls": [{"name": "read", "arguments": {"path": "tools/guard.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"tools/guard.py"}}</tool_call>',
             "prompt_tokens": 5000, "completion_tokens": 600},
            {"tool_calls": [{"name": "read", "arguments": {"path": "tools/fs.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"tools/fs.py"}}</tool_call>',
             "prompt_tokens": 5900, "completion_tokens": 500},
        ],
        "final": (
            "根据我最近读取的文件，以下是模块分析：\n\n"
            "### tools/guard.py — 注入防护\n"
            "使用 <external> 边界隔离外部数据。\n\n"
            "### tools/fs.py — 文件读写\n"
            "read 带行号输出，write 覆盖写入。\n\n"
            "（注：早期读取的 loop.py、permissions.py 内容已被后续大量输出挤出上下文，"
            "最终答复缺失这两个核心模块的分析，也未描述模块间协作关系。）"
        ),
    },
]


# ============================================================================
# 消融 3：有/无 observation 截断（Day7）
# ============================================================================

# A 组：有截断 —— 长 tool output 被 truncate_observation 截断到 4000 字符，
# token 可控，agent 基于可见部分做分析并明确提及截断
GROUP_WITH_TRUNCATION: list[dict[str, Any]] = [
    {
        "task": "large-file",
        "steps": [
            {"tool_calls": [{"name": "read", "arguments": {"path": "tools/code_analysis.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"tools/code_analysis.py"}}</tool_call>',
             "prompt_tokens": 350, "completion_tokens": 4200},
        ],
        "final": (
            "文件 tools/code_analysis.py 内容过长，仅显示了前 100000 字节（共 152340 字符）。\n\n"
            "## 基于可见部分的分析\n\n"
            "该文件包含 8 个代码分析工具，分 3 类：\n"
            "1. **结构分析**：repo_structure（目录树 + 文件清单）、mermaid_diagram（Mermaid 架构图）\n"
            "2. **质量扫描**：static_scan（pylint/radon 包装）、code_analyze（AST 级分析）、test_runner（pytest 包装）\n"
            "3. **搜索/追踪**：code_search（正则搜索）、dep_graph（import 依赖图）、generate_diff（git diff 包装）\n\n"
            "注意：文件末尾部分被截断，可能遗漏了部分辅助函数的实现细节。"
        ),
    },
]

# B 组：无截断 —— 整份 tool output 灌入上下文，token 严重超标，
# agent 被海量信息淹没，答复质量反而下降
GROUP_NO_TRUNCATION: list[dict[str, Any]] = [
    {
        "task": "large-file",
        "steps": [
            {"tool_calls": [{"name": "read", "arguments": {"path": "tools/code_analysis.py"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"tools/code_analysis.py"}}</tool_call>',
             "prompt_tokens": 350, "completion_tokens": 12000},  # 未截断，token 暴增 3x
        ],
        "final": (
            "tools/code_analysis.py 有很多代码分析工具。（上下文因单次读取就超过 12000 token 而严重膨胀，"
            "后续推理空间被挤压，无法给出详细分析。）"
        ),
    },
]


# ============================================================================
# 消融 4：有/无 MCP 工具（Day10）
# ============================================================================

# A 组：有 MCP 工具 —— Agent 能调用 mcp__echo 验证集成，扩展能力可用
GROUP_WITH_MCP: list[dict[str, Any]] = [
    {
        "task": "mcp-integration",
        "steps": [
            {"tool_calls": [{"name": "mcp__echo", "arguments": {"text": "hello from ablation test"}}],
             "raw": '<tool_call>{"name":"mcp__echo","arguments":{"text":"hello from ablation test"}}</tool_call>',
             "prompt_tokens": 300, "completion_tokens": 42},
        ],
        "final": (
            "✅ MCP 集成验证通过。mcp__echo 工具返回：'hello from ablation test'。"
            "MCP 协议客户端正常工作，外部工具已成功注册到 ToolRegistry。"
        ),
    },
]

# B 组：无 MCP 工具 —— Agent 只有内置工具，遇到需要外部集成的任务时受限
GROUP_NO_MCP: list[dict[str, Any]] = [
    {
        "task": "mcp-integration",
        "steps": [
            {"tool_calls": [{"name": "bash", "arguments": {"command": "echo 'hello from ablation test'"}}],
             "raw": '<tool_call>{"name":"bash","arguments":{"command":"echo hello from ablation test"}}</tool_call>',
             "prompt_tokens": 280, "completion_tokens": 38},
        ],
        "final": (
            "无法调用 mcp__echo 工具——当前环境未配置 MCP server。"
            "用 bash echo 模拟了类似功能，输出：'hello from ablation test'。"
            "但这不是真正的 MCP 集成验证，MCPClient 未启动。"
        ),
    },
]


# ============================================================================
# 消融 5：有/无 Skill 加载（Day10）
# ============================================================================

# A 组：有 Skill 加载 —— repo-onboarding skill 注入系统提示词，
# Agent 按结构化工作流执行：盘点→依赖→定位→验证→重排→README
GROUP_WITH_SKILLS: list[dict[str, Any]] = [
    {
        "task": "structured-guide",
        "steps": [
            {"tool_calls": [{"name": "glob", "arguments": {"pattern": "**/*.py"}}],
             "raw": '<tool_call>{"name":"glob","arguments":{"pattern":"**/*.py"}}</tool_call>',
             "prompt_tokens": 420, "completion_tokens": 35},
            {"tool_calls": [{"name": "repo_structure", "arguments": {"root_path": ".", "max_depth": 3}}],
             "raw": '<tool_call>{"name":"repo_structure","arguments":{"root_path":".","max_depth":3}}</tool_call>',
             "prompt_tokens": 600, "completion_tokens": 280},
            {"tool_calls": [{"name": "read", "arguments": {"path": "requirements.txt"}}],
             "raw": '<tool_call>{"name":"read","arguments":{"path":"requirements.txt"}}</tool_call>',
             "prompt_tokens": 1100, "completion_tokens": 25},
            {"tool_calls": [{"name": "grep", "arguments": {"pattern": "def main|__name__.*__main__", "path": "."}}],
             "raw": '<tool_call>{"name":"grep","arguments":{"pattern":"def main|__name__.*__main__","path":"."}}</tool_call>',
             "prompt_tokens": 1350, "completion_tokens": 45},
        ],
        "final": (
            "## mini-OpenClaw 代码库导读报告\n\n"
            "### 1. 项目概览\n"
            "- 语言：Python 3.11\n"
            "- 依赖：httpx, pydantic, markdownify, pylint, radon\n"
            "- 规模：7 个模块，24 个内置工具\n\n"
            "### 2. 模块架构（4 层）\n"
            "| 层 | 目录 | 职责 |\n"
            "|----|------|------|\n"
            "| LLM 交互层 | backend/ | DeepSeek API 客户端 + 消息归一化 |\n"
            "| 主循环层 | agent/ | ReAct loop + permissions + context + memory |\n"
            "| 工具执行层 | tools/ + mcp/ + skills/ | 24 内置工具 + MCP 扩展 + Skills 注入 |\n"
            "| 评估基座 | eval/ | 轨迹评测 + 消融实验 |\n\n"
            "### 3. 入口点\n"
            "- CLI 入口：agent/cli.py → main()\n"
            "- 测试入口：mcp/echo_server.py\n\n"
            "### 4. 依赖关系\n"
            "agent/cli.py → agent/loop.py → backend/client.py → DeepSeek API\n"
            "agent/loop.py → agent/permissions.py → tools/* (权限裁决 → 工具执行)\n\n"
            "### 5. 关键约定\n"
            "- 工具命名：内置工具裸名，MCP 工具 mcp__ 前缀\n"
            "- 消息格式：{role, content, tool_calls[]}，backend/client.py 负责 OpenAI 转换\n"
            "- 权限不可绕过：deny 绝对禁止，--auto-approve 仅跳过 confirm"
        ),
    },
]

# B 组：无 Skill 加载 —— Agent 缺乏领域知识，做通用分析，
# 缺少结构化工作流，遗漏关键架构信息
GROUP_NO_SKILLS: list[dict[str, Any]] = [
    {
        "task": "structured-guide",
        "steps": [
            {"tool_calls": [{"name": "glob", "arguments": {"pattern": "**/*.py"}}],
             "raw": '<tool_call>{"name":"glob","arguments":{"pattern":"**/*.py"}}</tool_call>',
             "prompt_tokens": 350, "completion_tokens": 35},
            {"tool_calls": [{"name": "bash", "arguments": {"command": "ls -la"}}],
             "raw": '<tool_call>{"name":"bash","arguments":{"command":"ls -la"}}</tool_call>',
             "prompt_tokens": 580, "completion_tokens": 60},
        ],
        "final": (
            "这个项目有以下几个目录：agent/、backend/、tools/、eval/、mcp/、skills/。\n"
            "看起来是个 Python 项目，有一些 Python 文件。\n"
            "建议你自己看看各个目录里的文件来了解具体功能。"
        ),
    },
]


# ============================================================================
# 消融注册表 + 运行器
# ============================================================================

# 每项消融的元数据：名称、变量描述、固定因素、A/B 组数据、判据任务集
ABLATION_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "system-prompt",
        "title":  "system-prompt 有/无",
        "variable": "system-prompt",
        "fixed":    "任务集 + 模型",
        "groups": [
            ("有 system-prompt", GROUP_WITH_SYS, SAMPLE_TASKS),
            ("无 system-prompt", GROUP_NO_SYS, SAMPLE_TASKS),
        ],
    },
    {
        "name": "compaction",
        "title":  "context compaction 有/无 (Day7)",
        "variable": "maybe_compact 上下文压缩",
        "fixed":    "任务集 + 模型 + 其他工具保持不变",
        "groups": [
            ("有 compaction", GROUP_WITH_COMPACTION, ABLATION_TASKS_DAY7),
            ("无 compaction", GROUP_NO_COMPACTION, ABLATION_TASKS_DAY7),
        ],
    },
    {
        "name": "truncation",
        "title":  "observation 截断 有/无 (Day7)",
        "variable": "truncate_observation (max_chars=4000)",
        "fixed":    "任务集 + 模型 + 其他工具保持不变",
        "groups": [
            ("有截断", GROUP_WITH_TRUNCATION, ABLATION_TASKS_DAY7),
            ("无截断", GROUP_NO_TRUNCATION, ABLATION_TASKS_DAY7),
        ],
    },
    {
        "name": "mcp",
        "title":  "MCP 工具有/无 (Day10)",
        "variable": "MCP 协议集成 + mcp__echo 工具",
        "fixed":    "任务集 + 模型 + 其他工具保持不变",
        "groups": [
            ("有 MCP", GROUP_WITH_MCP, ABLATION_TASKS_DAY10),
            ("无 MCP", GROUP_NO_MCP, ABLATION_TASKS_DAY10),
        ],
    },
    {
        "name": "skills",
        "title":  "Skill 加载有/无 (Day10)",
        "variable": "repo-onboarding skill 注入",
        "fixed":    "任务集 + 模型 + 其他工具保持不变",
        "groups": [
            ("有 Skills", GROUP_WITH_SKILLS, ABLATION_TASKS_DAY10),
            ("无 Skills", GROUP_NO_SKILLS, ABLATION_TASKS_DAY10),
        ],
    },
]


def run_all_ablations(name_filter: str | None = None) -> None:
    """执行全部（或指定）消融实验并打印完整报告。

    Args:
        name_filter: 可选，只跑名称匹配的消融（如 "compaction"）。
    """
    n_total = 0
    for entry in ABLATION_REGISTRY:
        if name_filter and name_filter != entry["name"]:
            continue
        n_total += 1

        # 汇总各组指标
        results: list[dict[str, Any]] = []
        for label, group, tasks in entry["groups"]:
            r = summarize(label, group, tasks)
            results.append(r)

        # 打印消融报告
        ablation_report(entry["title"], entry["variable"], entry["fixed"], results)

    if n_total == 0:
        print(f"[消融] 未找到匹配 '{name_filter}' 的消融实验。")
        print(f"  可用：{', '.join(e['name'] for e in ABLATION_REGISTRY)}")


def _parse_args(argv: list[str] | None = None) -> dict[str, Any]:
    """极简参数解析（不依赖 argparse，方便在 notebook 里用）。"""
    import sys as _sys
    args = argv or _sys.argv[1:]
    opts: dict[str, Any] = {"name": None, "help": False}
    i = 0
    while i < len(args):
        if args[i] in ("-h", "--help"):
            opts["help"] = True
        elif args[i] in ("-n", "--name") and i + 1 < len(args):
            i += 1
            opts["name"] = args[i]
        i += 1
    return opts


def main(argv: list[str] | None = None) -> int:
    """消融实验 CLI 入口。

    用法：
      python -m eval.ablation                  # 运行全部 5 项消融
      python -m eval.ablation --name compaction  # 只跑 compaction 消融
    """
    opts = _parse_args(argv)

    if opts["help"]:
        print("用法: python -m eval.ablation [--name <消融名>]")
        print()
        print("可用消融:")
        for entry in ABLATION_REGISTRY:
            print(f"  {entry['name']:<15s}  {entry['title']}")
        print()
        print("示例:")
        print("  python -m eval.ablation                    # 跑全部")
        print("  python -m eval.ablation --name compaction  # 只跑 Day7 compaction")
        print("  python -m eval.ablation --name skills      # 只跑 Day10 skills")
        return 0

    print("=" * 62)
    print("  mini-OpenClaw 消融实验 — 全部维度")
    print("=" * 62)
    print(f"  维度数: {len(ABLATION_REGISTRY)}")
    print("  注意: 当前轨迹数据为手工构造样本，真轨迹运行后替换。")
    print()

    run_all_ablations(name_filter=opts.get("name"))

    print("=" * 62)
    print("  消融实验完成。")
    print("  Day7 结论: compaction 控制 token 增长 + truncation 防止单次溢出")
    print("  Day10 结论: MCP 扩展工具边界 + Skills 注入领域工作流")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
