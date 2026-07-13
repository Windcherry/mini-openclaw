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

    def run(self, user_task: str) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_task},
        ]
        for turn in range(self.max_turns):
            try:
                assistant = self.backend.chat(messages, tools=self.registry.schemas())
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code == 401:
                    return "[错误] API Key 无效（401）。请检查 DEEPSEEK_API_KEY 环境变量是否正确、是否过期。"
                elif code == 429:
                    return "[错误] API 请求频率超限（429）。请稍后重试。"
                elif 500 <= code < 600:
                    return f"[错误] API 服务端异常（{code}）。请稍后重试或检查 DeepSeek 服务状态。"
                else:
                    return f"[错误] API 请求失败（{code}）：{e}"
            except httpx.TimeoutException:
                return "[错误] API 请求超时。请检查网络连接或稍后重试。"
            except httpx.ConnectError as e:
                return f"[错误] 无法连接 API 服务：{e}"
            messages.append({"role": "assistant",
                             "content": assistant.get("content", ""),
                             "tool_calls": assistant.get("tool_calls", [])})

            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                return assistant.get("content", "")

            # 分发并执行工具，把每个结果作为 role="tool" 注入 messages
            for call in tool_calls:
                tool_name = call["name"]
                tool_args = call.get("arguments", {})
                tool = self.registry.get(tool_name)
                if tool is None:
                    obs = f"错误：未知工具 {tool_name}"
                else:
                    # 权限层：在"解析出工具调用 → 真正执行"之间插入检查
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
                # 超长 observation 截断后再注入，避免瞬时撑爆上下文
                obs = truncate_observation(str(obs))
                messages.append({"role": "tool", "name": call["name"],
                                 "tool_call_id": call.get("id"), "content": obs})

            # 上下文管理：超出 token 预算时触发 compaction
            messages = maybe_compact(messages, self.backend)

        return "[达到最大轮数上限，未完成任务]"
