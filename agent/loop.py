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

Tracer 集成：每次 LLM 调用与工具执行记入 span，可 replay 回放 + cost_report 核算。
并行工具执行：多工具调用时用 ThreadPoolExecutor 并发执行（讲义 §7.2）。
"""
from __future__ import annotations
import json
from typing import Any

import httpx
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tools.base import ToolRegistry
from .context import maybe_compact, truncate_observation
from .permissions import check as permissions_check
from .tracer import Tracer

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


def _tool_call_display(call: dict) -> str:
    """格式化工具调用为可显示字符串（工具名 + JSON 参数）。"""
    name = call.get("name", "?")
    args = call.get("arguments", {})
    args_json = json.dumps(args, ensure_ascii=False, indent=2)
    return f"  🔧 {name}\n  {args_json}"


# ══════════════════════════════════════════════════════════════════════
# 并行工具执行辅助
# ══════════════════════════════════════════════════════════════════════

def _exec_tool_parallel(exec_tasks: list[tuple[dict, Any, dict]],
                        tracer: Tracer | None = None) -> list[tuple[dict, str]]:
    """用线程池并发执行多个工具调用。

    Args:
        exec_tasks: [(call_dict, tool_instance, tool_args), ...]
        tracer: 可选 Tracer，有则每个工具执行记一个 span

    Returns:
        [(call_dict, observation_str), ...]，顺序与输入一致
    """
    if len(exec_tasks) <= 1:
        # 单个工具：直接执行，不开线程池
        call, tool, tool_args = exec_tasks[0]
        if tracer:
            obs = tracer.span("tool", call["name"],
                              lambda t=tool, ta=tool_args: _run_tool_safely(t, ta))
        else:
            obs = _run_tool_safely(tool, tool_args)
        return [(call, str(obs))]

    # 多个工具：并行执行
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 构建 call_id → call 映射（保持消息顺序）
    future_to_call: dict = {}
    with ThreadPoolExecutor(max_workers=min(len(exec_tasks), 4)) as ex:
        for call, tool, tool_args in exec_tasks:
            def _runner(t=tool, ta=tool_args, name=call["name"]):
                if tracer:
                    return tracer.span("tool", name,
                                       lambda t2=t, ta2=ta: _run_tool_safely(t2, ta2))
                else:
                    return _run_tool_safely(t, ta)
            future = ex.submit(_runner)
            future_to_call[future] = call

    # 收集结果（保持提交顺序）
    results: list[tuple[dict, str]] = []
    for future in as_completed(future_to_call):
        call = future_to_call[future]
        try:
            obs = str(future.result())
        except Exception as e:
            obs = f"[并行执行异常] {call['name']}: {e}"
        results.append((call, obs))

    return results



class AgentLoop:
    def __init__(self, backend: Any, registry: ToolRegistry, system_prompt: str,
                 max_turns: int = 100, workdir: Path = WORKDIR,
                 auto_approve: bool = False, tracer: Tracer | None = None):
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.workdir = workdir.resolve()
        self.auto_approve = auto_approve
        self.tracer = tracer
        # 长程控制（讲义 §8.2）：绕圈 / 停滞检测
        from .planning import LoopDetector
        self._loop_detector = LoopDetector()

    def plan(self, task: str) -> str:
        """生成任务的执行计划（不调用工具）。

        向模型发送规划专用 prompt，不传 tools 列表，
        强制模型只输出文本计划。
        """
        from .prompts import PLAN_MODE_PROMPT
        plan_messages = [
            {"role": "system", "content": PLAN_MODE_PROMPT},
            {"role": "user", "content": task},
        ]
        try:
            assistant = self.backend.chat(plan_messages, tools=[])
            return assistant.get("content", "") or "[规划阶段] 模型未返回有效计划。"
        except Exception as e:
            return f"[规划失败] {e}"

    def _execute_turn(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        """执行一轮 ReAct：调用后端 → 执行工具 → 注入 observation。
        返回 (更新后的 messages, 最终答复或空字符串)。
        若 tool_calls 为空，返回的字符串即为最终答复；否则需继续循环。
        """
        turn_messages = list(messages)

        # ── 计算注入位置：如果存在执行计划（# 执行计划），则注入到计划之后 ──
        _PLAN_MARKER = "# 执行计划"
        _TODO_MARKER = "# 当前任务清单"
        _insert_pos = 1
        if (len(turn_messages) > 1
                and turn_messages[1].get("role") == "system"
                and turn_messages[1].get("content", "").startswith(_PLAN_MARKER)):
            _insert_pos = 2

        # ── Todo 注入：每轮把当前任务清单拼进上下文（防漂移，讲义 §8.3）──
        # 先清除上一轮的旧注入，再插入到 system prompt（或计划）之后，避免累积重复。
        turn_messages = [m for m in turn_messages
                         if not (m.get("role") == "system" and m.get("content", "").startswith(_TODO_MARKER))]
        try:
            from tools.more_tools import TODO
            todo_snapshot = ""
            if TODO.items:
                turn_messages.insert(_insert_pos, {
                    "role": "system",
                    "content": f"{_TODO_MARKER}（推进它，别跑偏）\n{TODO.render()}",
                })
                todo_snapshot = TODO.snapshot_status()
        except ImportError:
            todo_snapshot = ""

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
            turn_messages.insert(_insert_pos, {
                "role": "system",
                "content": "\n".join(warnings),
            })

        # ── LLM 调用（包入 tracer span）──
        def _call_backend():
            return self.backend.chat(turn_messages, tools=self.registry.schemas())

        try:
            if self.tracer:
                assistant = self.tracer.span("llm", "decide", _call_backend, tokens=None)
            else:
                assistant = _call_backend()
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            # 尝试提取 API 返回的详细错误信息
            detail = ""
            try:
                body = e.response.json()
                detail = body.get("error", {}).get("message", "")
            except Exception:
                pass
            if code == 401:
                return turn_messages, "[错误] API Key 无效（401）。请检查 DEEPSEEK_API_KEY 环境变量是否正确、是否过期。"
            elif code == 429:
                return turn_messages, "[错误] API 请求频率超限（429）。请稍后重试。"
            elif 500 <= code < 600:
                return turn_messages, f"[错误] API 服务端异常（{code}）。请稍后重试或检查 DeepSeek 服务状态。"
            else:
                msg = f"[错误] API 请求失败（{code}）"
                if detail:
                    msg += f"：{detail}"
                return turn_messages, msg
        except httpx.TimeoutException:
            return turn_messages, "[错误] API 请求超时。请检查网络连接或稍后重试。"
        except httpx.ConnectError as e:
            return turn_messages, f"[错误] 无法连接 API 服务：{e}"

        # ── 从 assistant 响应中提取 usage 并补入最近一个 llm span ──
        if self.tracer and self.tracer.last_span and self.tracer.last_span["kind"] == "llm":
            usage = assistant.get("usage", {})
            if usage:
                self.tracer.last_span["tokens"] = usage.get("total_tokens", 0)
                self.tracer.last_span["prompt_tokens"] = usage.get("prompt_tokens", 0)
                self.tracer.last_span["completion_tokens"] = usage.get("completion_tokens", 0)

        turn_messages.append({"role": "assistant",
                              "content": assistant.get("content", ""),
                              "tool_calls": assistant.get("tool_calls", [])})

        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            return turn_messages, assistant.get("content", "")

        # ── 工具执行（权限检查 + 并行执行 + tracer span）──
        exec_tasks: list[tuple[dict, Any, dict]] = []  # 可并行执行的任务

        for call in tool_calls:
            tool_name = call["name"]
            tool_args = call.get("arguments", {})
            tool = self.registry.get(tool_name)
            if tool is None:
                obs = f"错误：未知工具 {tool_name}"
                obs = truncate_observation(obs)
                turn_messages.append({"role": "tool", "name": call["name"],
                                      "tool_call_id": call.get("id"), "content": obs})
                continue

            verdict = permissions_check(tool_name, tool_args, self.workdir)
            if verdict == "deny":
                obs = f"[权限层] 拒绝：越界写入或危险操作 —— {tool_name}({tool_args})"
                obs = truncate_observation(obs)
                print(_tool_call_display(call))
                print(f"  🚫 权限拒绝")
                turn_messages.append({"role": "tool", "name": call["name"],
                                      "tool_call_id": call.get("id"), "content": obs})
            elif verdict == "confirm" and not self.auto_approve:
                # 需要用户确认 —— 同步处理，不并行
                print(f"\n  ⚠  工具 {tool_name} 需要确认：")
                for k, v in tool_args.items():
                    print(f"      {k}: {v}")
                answer = input(f"      是否执行？[y/N] ").strip().lower()
                if answer in ("y", "yes"):
                    if self.tracer:
                        obs = self.tracer.span("tool", tool_name,
                                               lambda t=tool, ta=tool_args: _run_tool_safely(t, ta))
                    else:
                        obs = _run_tool_safely(tool, tool_args)
                    print(_tool_call_display(call))
                    print(f"  → {str(obs)[:120].strip()}")
                else:
                    # 用户拒绝 → 终止整个任务，不给模型绕过的机会
                    return turn_messages, (
                        f"[已取消] 用户拒绝了 {tool_name} 操作，任务中断。\n"
                        f"参数：{tool_args}"
                    )
                obs = truncate_observation(str(obs))
                turn_messages.append({"role": "tool", "name": call["name"],
                                      "tool_call_id": call.get("id"), "content": obs})
            else:
                # allow 或 confirm+auto_approve → 可安全并行
                exec_tasks.append((call, tool, tool_args))

        # ── 批量并行执行工具 ──
        if exec_tasks:
            results = _exec_tool_parallel(exec_tasks, self.tracer)
            for call, obs in results:
                obs = truncate_observation(str(obs))
                print(_tool_call_display(call))
                print(f"  → {obs[:120].strip()}")
                turn_messages.append({"role": "tool", "name": call["name"],
                                      "tool_call_id": call.get("id"), "content": obs})

        # ── 绕圈 / 停滞检测：记录每步工具调用（讲义 §8.2）──
        for call in tool_calls:
            try:
                self._loop_detector.feed(call["name"], call.get("arguments", {}), todo_snapshot)
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
