"""ReAct 主循环（Agent 的心脏）。

  while 没到最终答复:
      assistant = backend.chat(messages, tools)     # 模型思考 or 调工具
      if assistant 有 tool_calls:
          for call in tool_calls:
              permissions.check() > deny/confirm   # Day10 权限关卡
              obs = tool.run(**arguments)           # 执行工具（含重试 + 错误分类）
              messages.append(tool_result(obs))     # 注入 observation
      else:
          return assistant.content                  # 最终答复

Day11+ 集成：todo 注入每轮上下文 + 错误恢复（TransientError 重试退避 /
PermanentError 引导重规划 / 反复失败 → block）。
"""
from __future__ import annotations
from typing import Any

import httpx
import time

from pathlib import Path

from tools.base import ToolRegistry
from .context import maybe_compact, truncate_observation
from .permissions import check as permissions_check

WORKDIR = Path.cwd().resolve()


# ══════════════════════════════════════════════════════════════════════
# 错误分类（讲义 §7）
# ══════════════════════════════════════════════════════════════════════

def _classify_error(e: Exception) -> str:
    """将异常分为 'transient'（瞬时，可重试）或 'permanent'（永久，需重规划）。"""
    # httpx 网络层异常 → 瞬时
    if isinstance(e, (httpx.TimeoutException, httpx.ConnectError,
                       httpx.RemoteProtocolError, httpx.NetworkError)):
        return "transient"
    # ConnectionError / TimeoutError 等标准库异常 → 瞬时
    if isinstance(e, (ConnectionError, TimeoutError)):
        return "transient"
    msg = str(e).lower()
    transient_kw = ("timeout", "connection", "rate limit", "429", "503",
                    "temporary", "retry", "network", "reset", "refused")
    if any(kw in msg for kw in transient_kw):
        return "transient"
    # 其他一切 → 永久（文件不存在、语法错误、权限不足等）
    return "permanent"


def _run_tool_safely(tool: Any, tool_args: dict, max_retries: int = 3) -> str:
    """执行工具，瞬时错误自动重试（指数退避），永久错误直接返回。

    重试耗尽时返回带 [重试耗尽] 标记的消息，引导模型 re-plan 或 block。
    """
    last_error: str = ""

    for k in range(max_retries):
        try:
            return tool.run(**tool_args)
        except Exception as e:
            last_error = str(e)
            error_type = _classify_error(e)
            if error_type == "transient" and k < max_retries - 1:
                wait = 0.5 * (2 ** k)
                time.sleep(wait)
                continue
            elif error_type == "transient":
                # 重试耗尽
                return (
                    f"[重试耗尽] 工具 {tool.name} 连续失败 {max_retries} 次（瞬时错误）。\n"
                    f"最后一次错误：{last_error}\n"
                    f"建议：将此子任务标记为 blocked，先推进其他任务。"
                )
            else:
                # 永久错误：不重试，直接返回
                return (
                    f"[永久错误] 工具 {tool.name} 执行失败。\n"
                    f"错误：{last_error}\n"
                    f"建议：分析原因，调整方案后重试。如涉及依赖缺失，先完成依赖子任务。"
                )

    return f"[重试耗尽] {last_error}"



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
        # 长程控制（讲义 §8.2）：绕圈 / 停滞检测
        from .planning import LoopDetector
        self._loop_detector = LoopDetector()

    def _execute_turn(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        """执行一轮 ReAct：调用后端 → 执行工具 → 注入 observation。
        返回 (更新后的 messages, 最终答复或空字符串)。
        若 tool_calls 为空，返回的字符串即为最终答复；否则需继续循环。
        """
        turn_messages = list(messages)

        # ── Todo 注入：每轮把当前任务清单拼进上下文（防漂移，讲义 §8.3）──
        # 先清除上一轮的旧注入，再追加最新状态，避免累积重复
        _TODO_MARKER = "# 当前任务清单"
        turn_messages = [m for m in turn_messages
                         if not (m.get("role") == "system" and m.get("content", "").startswith(_TODO_MARKER))]
        try:
            from tools.more_tools import TODO
            todo_snapshot = ""
            if TODO.items:
                turn_messages.append({
                    "role": "system",
                    "content": f"{_TODO_MARKER}（推进它，别跑偏）\n{TODO.render()}",
                })
                todo_snapshot = TODO.snapshot_status()
        except ImportError:
            pass

        # ── 绕圈 / 停滞警告注入（讲义 §8.2）──
        loop_warn = self._loop_detector.is_looping()
        stall_warn = self._loop_detector.is_stalled()
        if loop_warn or stall_warn:
            warnings: list[str] = []
            if loop_warn:
                warnings.append(f"⚠️ {loop_warn}")
            if stall_warn:
                warnings.append(f"⚠️ {stall_warn}")
            warnings.append("请停止当前策略，重新规划下一步。")
            turn_messages.append({
                "role": "system",
                "content": "\n".join(warnings),
            })

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
                elif verdict == "confirm":
                    if self.auto_approve:
                        obs = _run_tool_safely(tool, tool_args)
                    else:
                        # 需要用户确认
                        print(f"\n  ⚠  工具 {tool_name} 需要确认：")
                        for k, v in tool_args.items():
                            print(f"      {k}: {v}")
                        answer = input(f"      是否执行？[y/N] ").strip().lower()
                        if answer in ("y", "yes"):
                            obs = _run_tool_safely(tool, tool_args)
                        else:
                            # 用户拒绝 → 终止整个任务，不给模型绕过的机会
                            return turn_messages, (
                                f"[已取消] 用户拒绝了 {tool_name} 操作，任务中断。\n"
                                f"参数：{tool_args}"
                            )
                else:
                    obs = _run_tool_safely(tool, tool_args)

            obs = truncate_observation(str(obs))
            turn_messages.append({"role": "tool", "name": call["name"],
                                  "tool_call_id": call.get("id"), "content": obs})

            # ── 绕圈 / 停滞检测：记录每步工具调用（讲义 §8.2）──
            try:
                self._loop_detector.feed(tool_name, tool_args, todo_snapshot)
            except Exception:
                pass

        return turn_messages, ""

    def chat(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        """多轮对话入口：在已有 messages 基础上继续 ReAct 循环。
        返回 (更新后的 messages, 最终答复)。

        终止条件（讲义 §8.1/§8.3）：
          1. 模型返回无 tool_calls 的最终答复 + TODO.all_done()
          2. 达到 max_turns 上限 → 汇报当前进度
        """
        for step in range(self.max_turns):
            messages, final = self._execute_turn(messages)
            if final:
                # 尝试检测任务完成度
                try:
                    from tools.more_tools import TODO
                    if TODO.items:
                        if TODO.all_done():
                            return messages, final
                        # 有 todo 但未全部完成 → 追加进度提示
                        remaining = len(TODO.active_tasks())
                        final += f"\n\n📋 {TODO.summary()}（{remaining} 项未完成）"
                except ImportError:
                    pass
                return messages, final
            messages = maybe_compact(messages, self.backend)

        # ── 步数上限：汇报当前状态，不无声燃烧 ──
        status_lines = [f"[达到步数上限 {self.max_turns}，任务未完成]"]
        try:
            from tools.more_tools import TODO
            if TODO.items:
                status_lines.append(f"\n当前进度：\n{TODO.render()}")
                status_lines.append(f"\n{TODO.summary()}")
                # 建议下一步
                next_id = TODO.next_pending()
                if next_id:
                    status_lines.append(f"建议：从子任务 #{next_id} 继续。")
        except ImportError:
            pass
        return messages, "\n".join(status_lines)

    def run(self, user_task: str) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_task},
        ]
        _, final = self.chat(messages)
        return final
