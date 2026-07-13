"""ReAct 主循环（Agent 的心脏）。

  while 没到最终答复:
      assistant = backend.chat(messages, tools)      # 模型这一步：思考 or 调工具
      if assistant 有 tool_calls:
          for call in tool_calls:
              obs = registry.get(call.name).run(**call.arguments)   # 执行工具
              messages.append(tool_result(obs))                     # 注入 observation
      else:
          return assistant.content                                 # 最终答复

Day5 你要把下面的 run() 真正实现出来（Day6 随工具集扩展完善）。骨架已给出结构与防呆上限。
"""
from __future__ import annotations
from typing import Any

from tools.base import ToolRegistry
from .context import maybe_compact, truncate_observation


class AgentLoop:
    def __init__(self, backend: Any, registry: ToolRegistry, system_prompt: str,
                 max_turns: int = 20):
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns          # 防死循环：硬上限

    def run(self, user_task: str) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_task},
        ]
        for turn in range(self.max_turns):
            assistant = self.backend.chat(messages, tools=self.registry.schemas())
            messages.append({"role": "assistant",
                             "content": assistant.get("content", ""),
                             "tool_calls": assistant.get("tool_calls", [])})

            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                return assistant.get("content", "")

            # 分发并执行工具，把每个结果作为 role="tool" 注入 messages
            for call in tool_calls:
                tool = self.registry.get(call["name"])
                if tool is None:
                    obs = f"错误：未知工具 {call['name']}"
                else:
                    try:
                        obs = tool.run(**call.get("arguments", {}))
                    except Exception as e:
                        obs = f"工具执行异常：{e}\n请分析错误原因并重试。"
                # 超长 observation 截断后再注入，避免瞬时撑爆上下文
                obs = truncate_observation(str(obs))
                messages.append({"role": "tool", "name": call["name"],
                                 "tool_call_id": call.get("id"), "content": obs})

            # 上下文管理：超出 token 预算时触发 compaction
            messages = maybe_compact(messages, self.backend)

        return "[达到最大轮数上限，未完成任务]"
