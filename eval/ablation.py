"""最小消融：固定任务集，只改一个因素，对比两组样本轨迹。

D4 起用真轨迹替换这些构造样本，每组多次运行取均值再下结论。
这是 D4（量化上下文/压缩策略）的预演，也是最终 Demo 展示要用到的方法学。

当前消融维度：
  1. system-prompt 有无 —— 验证"告诉 agent 工具约定"是否必要
  （Day7 追加：compaction 有/无、observation 截断有/无）
  （Day10 追加：MCP 工具有/无、Skill 加载有/无）
"""
from __future__ import annotations
from typing import Any

from eval.tasks import SAMPLE_TASKS
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
# 使用方式：
#   from eval.ablation import summarize, ablation_report
#   a = summarize("有 system-prompt", group_a, tasks)
#   b = summarize("无 system-prompt", group_b, tasks)
#   ablation_report("system-prompt 有/无", "system-prompt", "任务集+模型", [a, b])
# ============================================================================
