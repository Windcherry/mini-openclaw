"""运行内轨迹采集器：把每次 LLM 调用与工具执行记成 span。

设计原则：
  - 一次运行 = 一串 span（llm / tool 交替）
  - span() 用 finally 保证即使 fn 抛异常也记录（调试必备）
  - 所有 span 在内存中，可 replay 回放、cost_report 核算成本

与 eval/tracer.py 的关系：
  - agent/tracer.py：内存 span，实时回放 + 成本，用于 Agent 自省和调试
  - eval/tracer.py：JSONL 文件追加，离线评估 + replay + 指标计算
"""
from __future__ import annotations
import time
from typing import Any, Callable


class Tracer:
    """单次运行内的 span 采集器。

    用法：
        tracer = Tracer()
        resp = tracer.span("llm", "decide", lambda: backend.chat(msgs), tokens=None)
        # … 拿到 usage 后补上 token 信息 …
        tracer.last_span["tokens"] = usage["total_tokens"]
        tracer.last_span["prompt_tokens"] = usage.get("prompt_tokens", 0)
        tracer.last_span["completion_tokens"] = usage.get("completion_tokens", 0)

        result = tracer.span("tool", "read", lambda: tool.run(**args))
    """

    def __init__(self):
        self.spans: list[dict[str, Any]] = []

    @property
    def last_span(self) -> dict[str, Any] | None:
        """最近一个 span 的引用，方便补字段（如 usage token 数）。"""
        return self.spans[-1] if self.spans else None

    def span(self, kind: str, name: str, fn: Callable[[], Any], **meta) -> Any:
        """执行 fn()，用 finally 保证无论成败都记 span。

        Args:
            kind: "llm" | "tool"
            name: 人类可读的短名（如 "decide"、"read"）
            fn:   要执行的 callable
            **meta: 附加字段，直接合并进 span dict

        Returns:
            fn() 的返回值（透传，不改变调用方语义）
        """
        t0 = time.time()
        ok, out = True, None
        try:
            out = fn()
            return out
        except Exception as e:
            ok, out = False, repr(e)
            raise
        finally:
            span: dict[str, Any] = {
                "kind": kind,
                "name": name,
                "ok": ok,
                "ms": round((time.time() - t0) * 1000),
                "out": str(out)[:500],
            }
            span.update(meta)
            self.spans.append(span)

    @property
    def step_count(self) -> int:
        """LLM 调用次数（即 ReAct 轮数）。"""
        return sum(1 for s in self.spans if s["kind"] == "llm")

    @property
    def tool_count(self) -> int:
        """工具执行次数。"""
        return sum(1 for s in self.spans if s["kind"] == "tool")

    @property
    def total_ms(self) -> int:
        """总耗时（毫秒）。"""
        return sum(s.get("ms", 0) for s in self.spans)


# ══════════════════════════════════════════════════════════════════════
# 回放（replay）：把一次运行渲染出来 —— 调试的起点
# ══════════════════════════════════════════════════════════════════════

def replay(tracer: Tracer) -> None:
    """把 tracer 中的所有 span 逐条打印，一眼看清"它到底怎么走的"。

    输出格式：
      #1 llm  decide      1234ms  520tok → {"role":"assistant","content":"...
      #2 tool read           5ms          → 已写入 42 字节到 ...
    """
    if not tracer.spans:
        print("  (无 span)")
        return
    for i, s in enumerate(tracer.spans, 1):
        tok = f"  {s.get('tokens', s.get('total_tokens', ''))}tok" if s.get("tokens") or s.get("total_tokens") else ""
        flag = "" if s["ok"] else "  \033[31m✗\033[0m"
        out_preview = s["out"].replace("\n", " ")[:60]
        print(
            f"  #{i:<3} {s['kind']:<4} {s['name']:<18} "
            f"{s['ms']:>5}ms{tok}  → {out_preview}{flag}"
        )
    print(f"  —— {len(tracer.spans)} span，{tracer.step_count} 轮 LLM，"
          f"{tracer.tool_count} 次工具，总耗时 {tracer.total_ms}ms")


# ══════════════════════════════════════════════════════════════════════
# 成本核算（token 统计 + 费用估算）
# ══════════════════════════════════════════════════════════════════════

def cost_report(tracer: Tracer, price_per_1k: float = 0.001) -> None:
    """打印 token 消耗与估算成本（讲义 §6）。

    DeepSeek 默认价格约 $0.001/1K tokens（实际以官网为准）。
    """
    llm_spans = [s for s in tracer.spans if s["kind"] == "llm"]

    # 总 token
    total_prompt = sum(s.get("prompt_tokens", 0) for s in llm_spans)
    total_completion = sum(s.get("completion_tokens", 0) for s in llm_spans)
    total = total_prompt + total_completion

    cost = total / 1000 * price_per_1k

    print(f"  总 prompt tokens:     {total_prompt:>8}")
    print(f"  总 completion tokens:  {total_completion:>8}")
    print(f"  总 tokens:             {total:>8}")
    print(f"  估算成本:              ${cost:.4f}  (@ ${price_per_1k}/1K tok)")

    # 最贵一步
    priciest = max(llm_spans, key=lambda s: s.get("prompt_tokens", 0) + s.get("completion_tokens", 0), default=None)
    if priciest:
        pt = priciest.get("prompt_tokens", 0)
        ct = priciest.get("completion_tokens", 0)
        print(f"  最贵一步: {priciest['name']}  span "
              f"(prompt={pt}, completion={ct}, total={pt + ct})")

    # 二次膨胀观察（讲义 §6.2）：打印每轮 prompt_tokens 趋势
    prompt_seq = [s.get("prompt_tokens", 0) for s in llm_spans if s.get("prompt_tokens")]
    if len(prompt_seq) > 1:
        print(f"  每轮 prompt_tokens 趋势: {prompt_seq}")
        if prompt_seq[-1] > prompt_seq[0]:
            ratio = f"{prompt_seq[-1] / max(prompt_seq[0], 1):.1f}x"
            print(f"    首轮→末轮膨胀: {prompt_seq[0]} → {prompt_seq[-1]} ({ratio})")
