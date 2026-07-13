"""ReAct 主循环（Agent 的心脏）。

  while 没到最终答复:
      assistant = backend.chat(messages, tools)     # 模型思考 or 调工具
      if assistant 有 tool_calls:
          for call in tool_calls:
              permissions.check() > deny/confirm   # Day10 权限关卡
              obs = tool.run(**arguments)           # 执行工具
              messages.append(tool_result(obs))     # 注入 observation
      else:
          return assistant.content                  # 最终答复
"""
from __future__ import annotations
from typing import Any

import httpx

from pathlib import Path

from tools.base import ToolRegistry
from .context import maybe_compact, truncate_observation
from .permissions import check as permissions_check

WORKDIR = Path.cwd().resolve()


class AgentLoop:
    def __init__(self, backend: Any, registry: ToolRegistry, system_prompt: str,
                 max_turns: int = 40, workdir: Path = WORKDIR,
                 auto_approve: bool = False):
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.workdir = workdir.resolve()
        self.auto_approve = auto_approve

    def _execute_turn(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        """执行一轮 ReAct：调用后端 → 执行工具 → 注入 observation。
        返回 (更新后的 messages, 最终答复或空字符串)。
        若 tool_calls 为空，返回的字符串即为最终答复；否则需继续循环。
        """
        turn_messages = list(messages)
        try:
            assistant = self.backend.chat(turn_messages, tools=self.registry.schemas())
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 401:
                return turn_messages, "[错误] API Key 无效（401）。请检查 DEEPSEEK_API_KEY 环境变量是否正确、是否过期。"
            elif code == 429:
                return turn_messages, "[错误] API 请求频率超限（429）。请稍后重试。"
            elif 500 <= code < 600:
                return turn_messages, f"[错误] API 服务端异常（{code}）。请稍后重试或检查 DeepSeek 服务状态。"
            else:
                return turn_messages, f"[错误] API 请求失败（{code}）：{e}"
        except httpx.TimeoutException:
            return turn_messages, "[错误] API 请求超时。请检查网络连接或稍后重试。"
        except httpx.ConnectError as e:
            return turn_messages, f"[错误] 无法连接 API 服务：{e}"

        turn_messages.append({"role": "assistant",
                              "content": assistant.get("content", ""),
                              "tool_calls": assistant.get("tool_calls", [])})

        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            return turn_messages, assistant.get("content", "")

        for call in tool_calls:
            tool_name = call["name"]
            tool_args = call.get("arguments", {})
            tool = self.registry.get(tool_name)
            if tool is None:
                obs = f"错误：未知工具 {tool_name}"
            else:
                verdict = permissions_check(tool_name, tool_args, self.workdir)
                if verdict == "deny":
                    obs = f"[权限层] 拒绝：越界写入或危险操作 —— {tool_name}({tool_args})"
                elif verdict == "confirm" and not self.auto_approve:
                    obs = (f"[权限层] 需确认：{tool_name}({tool_args}) —— "
                           f"已拦截（演示模式：默认不放行）")
                else:
                    try:
                        obs = tool.run(**tool_args)
                    except Exception as e:
                        obs = f"工具执行异常：{e}\n请分析错误原因并重试。"
            obs = truncate_observation(str(obs))
            turn_messages.append({"role": "tool", "name": call["name"],
                                  "tool_call_id": call.get("id"), "content": obs})

        return turn_messages, ""

    def chat(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        """多轮对话入口：在已有 messages 基础上继续 ReAct 循环。
        返回 (更新后的 messages, 最终答复)。
        """
        for _ in range(self.max_turns):
            messages, final = self._execute_turn(messages)
            if final:
                return messages, final
            messages = maybe_compact(messages, self.backend)
        return messages, "[达到最大轮数上限，未完成任务]"

    def run(self, user_task: str) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_task},
        ]
        _, final = self.chat(messages)
        return final
