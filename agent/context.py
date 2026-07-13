"""上下文管理（Day7）：token 预算、滑动窗口、自动摘要 / compaction。

模型上下文窗口有限。长任务里 messages 会越堆越长，迟早超预算。
策略：
  - 估算当前 messages 的 token 数；
  - 超过阈值时触发 compaction：把较早的对话摘要成一条 system 备忘，
    保留最近 K 轮原文 + 关键工具结果；
  - tool result 过长时先截断/摘要再注入。
"""
from __future__ import annotations
from typing import Any


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """粗估 token 数：字符数 / 4。

    同时计入 tool_call arguments 与 tool_call_id，避免漏算结构字段。
    """
    total = 0
    for m in messages:
        total += len(str(m.get("content", "")))
        for tc in m.get("tool_calls") or []:
            total += len(str(tc.get("arguments", "")))
            total += len(str(tc.get("id", "")))
            total += len(str(tc.get("name", "")))
        total += len(str(m.get("tool_call_id", "")))
        total += len(str(m.get("name", "")))
    return total // 4


def _summarize(backend, chunk: list[dict[str, Any]]) -> str:
    """调用后端把对话历史压缩成要点。"""
    text = "\n".join(f"{m['role']}: {m.get('content', '')}" for m in chunk)
    prompt = "把下面的对话历史压缩成要点，保留任务目标、关键发现、已完成步骤：\n" + text
    resp = backend.chat([{"role": "user", "content": prompt}], tools=[])
    return resp.get("content", "")


def maybe_compact(messages: list[dict[str, Any]], backend,
                  budget: int = 6000) -> list[dict[str, Any]]:
    """超预算则压缩历史，返回新的 messages。

    策略：
      1) 保留 messages[0]（system prompt）
      2) 找到最近 K=4 个 assistant 轮次，保留原文
      3) 把中间的 user/assistant/tool 调用后端摘要成一条 system 备忘
      4) 返回 [system] + [备忘] + [最近K轮]
    """
    if estimate_tokens(messages) <= budget:
        return messages

    K = 4  # 保留最近 K 个 assistant 轮次
    # 从后往前找 assistant 消息的下标
    assistant_indices = []
    for i in range(len(messages) - 1, 0, -1):
        if messages[i].get("role") == "assistant":
            assistant_indices.append(i)
        if len(assistant_indices) >= K:
            break

    if not assistant_indices:
        return messages  # 尚无 assistant 消息，无需压缩

    assistant_indices.reverse()  # 升序
    first_kept = assistant_indices[0]

    system = messages[0:1]            # 原始 system prompt
    recent = messages[first_kept:]    # 最近 K 轮原文
    middle = messages[1:first_kept]   # 待压缩的中间部分

    if not middle:
        return messages

    summary = _summarize(backend, middle)
    summary_msg = {"role": "system",
                   "content": f"历史备忘：\n{summary}"}

    return system + [summary_msg] + recent


def truncate_observation(text: str, max_chars: int = 4000) -> str:
    """工具结果过长时截断并提示。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[已截断，共 {len(text)} 字符]"
