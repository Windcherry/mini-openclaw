"""极小轨迹记录器：一步一行 JSON（JSONL），可回放。

设计原则：「先记录，后评估」。
  - 主循环每步调一次 tracer.log_step()，追加一行 JSON 到 .jsonl 文件。
  - 所有指标（token 开销、步数、JSON 合法率）本质上都是对这种结构化 trace 的事后聚合。
  - D4 起在 agent/loop.py 里接入 tracer，今天 eval/metrics.py 的指标就能直接作用到真轨迹上。

JSONL 格式（每行一个事件）：
  {"ts": 1712345678.123, "step": 0,
   "tool_calls": [{"name": "read", "arguments": {...}}],
   "prompt_tokens": 120, "completion_tokens": 35,
   "note": "..."}
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any


class Tracer:
    """追加式轨迹记录器。线程安全未做——Agent 单线程运行，无需加锁。"""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.write_text("", encoding="utf-8")   # 清空或新建
        self._step = 0

    def log_step(self, step: int, tool_calls: list[dict], prompt_tokens: int,
                 completion_tokens: int, note: str = "") -> None:
        """追加一条步骤事件到 JSONL。

        Args:
            step: 步骤序号（从 0 开始）
            tool_calls: 本步模型输出的工具调用列表
            prompt_tokens: 本步 prompt token 数
            completion_tokens: 本步 completion token 数
            note: 备注（通常截取 raw 文本的前几十字）
        """
        event: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "step": step,
            "tool_calls": tool_calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "note": note,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def replay(path: str) -> None:
    """把一条 JSONL 轨迹逐步打印出来（回放）。

    每行一个事件，按 step 字段顺序打印工具调用和 token 开销。
    """
    text = Path(path).read_text(encoding="utf-8")
    if not text.strip():
        print("  (空轨迹)")
        return

    total_tok = 0
    for line in text.splitlines():
        e = json.loads(line)
        tok = e.get("prompt_tokens", 0) + e.get("completion_tokens", 0)
        total_tok += tok
        names = [tc.get("name", "?") for tc in e.get("tool_calls", [])] or ["(无工具调用)"]
        print(
            f"  step {e['step']}: 调用 {names}  "
            f"| 本步 {tok:>4d} tok  | {e.get('note', '')}"
        )
    print(f"  —— 轨迹共 {total_tok} token")


def load_trajectory(path: str) -> dict[str, Any]:
    """从 JSONL 文件加载回完整轨迹 dict（兼容 eval/tasks.py 的 Trajectory 格式）。

    Returns:
        {"task": "从文件名推导", "steps": [...], "final": ""}
    """
    steps: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        steps.append({
            "tool_calls": e.get("tool_calls", []),
            "raw": e.get("note", ""),
            "prompt_tokens": e.get("prompt_tokens", 0),
            "completion_tokens": e.get("completion_tokens", 0),
        })

    # 从文件名推导任务名
    task_name = Path(path).stem.replace("trace_", "").replace("_", "-")
    return {
        "task": task_name,
        "steps": steps,
        "final": "",  # Tracer 不记录 final，需另行填入
    }

# ============================================================================
# 使用方式（供 agent/loop.py 调用）：
#   from eval.tracer import Tracer, replay, load_trajectory
#   tracer = Tracer("eval/traces/run_001.jsonl")
#   tracer.log_step(step=0, tool_calls=[...], prompt_tokens=120, completion_tokens=35)
#   replay("eval/traces/run_001.jsonl")
# ============================================================================
