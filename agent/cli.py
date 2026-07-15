"""命令行入口。

用法：
  python -m agent.cli --selfcheck          # 自检骨架是否装好
  python -m agent.cli "创建 hello.py 并运行"  # 单次任务
  python -m agent.cli --chat               # 多轮对话模式
  python -m agent.cli                      # 无参数 = 进入多轮对话
"""
from __future__ import annotations
import argparse
import atexit
import shutil
import sys
from pathlib import Path

from tools.base import build_default_registry
from agent.prompts import SYSTEM_PROMPT

# ── readline 设置：光标自由移动 + 上下箭头历史 ──────────────
# 在 Linux/WSL 上，导入 readline 即可激活 input() 的行编辑和历史功能。
# macOS 可能需要 gnureadline（pip install gnureadline），降级处理。
try:
    import readline
    _HAS_READLINE = True
    _HISTFILE = Path.home() / ".mini-openclaw_history"
    try:
        readline.read_history_file(str(_HISTFILE))
    except (FileNotFoundError, OSError):
        pass
    readline.set_history_length(1000)
    atexit.register(readline.write_history_file, str(_HISTFILE))
except ImportError:
    _HAS_READLINE = False


def _rl_prompt(text: str) -> str:
    """将 ANSI 转义序列包裹在 \\001 / \\002 中，readline 才能正确计算光标位置。

    不加这个包裹 → 左右箭头移动时光标会跳到错误位置（因为 readline 把 ANSI
    码也算进了 prompt 宽度）。加了之后 readline 知道这些字节不占显示宽度。
    """
    if not _HAS_READLINE:
        return text
    result: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\033":
            # ANSI 转义序列：从 ESC 开始到 [m 或类似终止符
            end = i + 1
            while end < len(text) and text[end] not in "mABCDEFGHJKSTfhlnsu":
                end += 1
            if end < len(text):
                end += 1  # 吃掉终止字符
            result.append("\001")
            result.append(text[i:end])
            result.append("\002")
            i = end
        else:
            result.append(text[i])
            i += 1
    return "".join(result)

# ── ANSI 颜色 ──────────────────────────────────────────────
BLUE = "\033[38;5;33m"       # 深海豹蓝
LIGHT = "\033[38;5;81m"      # 冰蓝（眼睛/鼻子高光）
TEAL = "\033[38;5;43m"       # 青绿（底部标题区）
WHITE = "\033[38;5;255m"     # 白
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

SEAL_NAME = rf"""
{TEAL}  ╭────────────────────────────╮
  │  {WHITE}{BOLD}mini-OpenClaw{RESET}{TEAL}             │
  │  {DIM}Repo Guide · 代码库导读{RESET}{TEAL}   │
  ╰────────────────────────────╯{RESET}
"""


def selfcheck() -> int:
    print("== mini-OpenClaw 自检 ==")
    ok = True
    try:
        reg = build_default_registry()
        print(f"[ok] 工具注册表加载成功，当前内置工具数：{len(reg)}")
    except Exception as e:  # noqa
        print(f"[FAIL] 工具注册表：{e}"); ok = False

    try:
        from backend.fake_backend import FakeBackend
        FakeBackend().chat([{"role": "user", "content": "hi"}], tools=[])
        print("[ok] FakeBackend 可用（未配 DEEPSEEK_API_KEY 时的离线占位后端）")
    except Exception as e:  # noqa
        print(f"[FAIL] FakeBackend：{e}"); ok = False

    try:
        from agent.loop import AgentLoop  # noqa
        print("[ok] 主循环模块可导入")
    except Exception as e:  # noqa
        print(f"[FAIL] 主循环：{e}"); ok = False

    print("== 自检", "通过 ✅" if ok else "未通过 ❌", "==")
    return 0 if ok else 1


def _build_system(task: str = "") -> str:
    """组装完整 system prompt（Skills + 记忆注入）。"""
    from skills.loader import load_skills, skills_catalog, match_skills
    skills = load_skills()
    system = SYSTEM_PROMPT + "\n\n# 可用 Skills（相关时按其流程执行）\n" + skills_catalog(skills)
    if task:
        matched = match_skills(task, skills)
        if matched:
            system += "\n\n# 已激活 Skill（任务命中，注入完整流程）\n"
            for s in matched:
                system += f"\n## {s.name}\n{s.body}\n"

    from agent.memory import Memory, KVMemory
    mem = Memory("MEMORY.md")
    recalled = mem.recall()
    if recalled.strip():
        system += "\n\n# 关于本项目 / 用户的已知记忆（相关时遵循）\n" + recalled
    kv = KVMemory("memory.json")
    kv_recalled = kv.recall()
    if kv_recalled.strip():
        system += "\n\n# 结构化项目记忆（按需参考）\n" + kv_recalled
    return system


def _build_agent(args: argparse.Namespace):
    """构建 AgentLoop 实例（注册表 + MCP + 后端 + 权限 + Tracer）。"""
    from agent.loop import AgentLoop
    from agent.tracer import Tracer
    reg = build_default_registry()

    from mcp.client import MCPClient, register_mcp_tools
    try:
        mcp = MCPClient(["python", "mcp/echo_server.py"])
        mcp.start()
        register_mcp_tools(reg, mcp)
    except Exception as e:
        pass  # MCP 静默失败，chat 模式不打扰

    backend_type = "deepseek"
    try:
        from backend.client import DeepSeekBackend
        backend = DeepSeekBackend()
    except Exception:
        from backend.fake_backend import FakeBackend
        backend = FakeBackend()
        backend_type = "fake"

    tracer = Tracer()
    return (AgentLoop(backend, reg, "", workdir=args.workdir,
                      auto_approve=args.auto_approve or args.dangerously_skip_permissions,
                      tracer=tracer), backend_type, tracer)


def _plan_and_confirm(agent, task: str, auto_approve: bool = False) -> str | None:
    """规划→展示→确认流程。返回确认后的计划文本，或 None（用户取消）。

    支持 [y]确认 / [e]修改（含反馈重新生成） / [n]取消。
    """
    print(f"\n  {DIM}📋 正在生成执行计划...{RESET}\n")
    plan_text = agent.plan(task)
    print(plan_text)
    print()

    if auto_approve:
        print(f"  {DIM}（--auto-approve 已启用，自动接受计划）{RESET}")
        return plan_text

    max_retries = 3
    for _ in range(max_retries):
        try:
            answer = input(_rl_prompt(
                f"  {BLUE}是否按此计划执行？{RESET} [y]确认 [e]修改 [n]取消: "
            )).strip().lower()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {DIM}已取消{RESET}")
            return None

        if answer in ("y", "yes"):
            return plan_text
        elif answer in ("n", "no"):
            print(f"  {DIM}已取消{RESET}")
            return None
        elif answer in ("e", "edit"):
            try:
                feedback = input(_rl_prompt(f"  {BLUE}修改意见：{RESET} ")).strip()
            except (KeyboardInterrupt, EOFError):
                print(f"\n  {DIM}已取消{RESET}")
                return None
            if not feedback:
                continue
            print(f"\n  {DIM}📋 正在更新计划...{RESET}\n")
            plan_text = agent.plan(
                f"原任务：{task}\n\n用户对上一版计划的修改意见：{feedback}\n\n请根据修改意见生成新的执行计划。"
            )
            print(plan_text)
            print()
        else:
            print(f"  {DIM}请输入 y/e/n{RESET}")

    # 达到最大修改次数，接受最后生成的计划
    print(f"  {DIM}（已达最大修改次数，接受最后生成的计划）{RESET}")
    return plan_text


def _chat_loop(args: argparse.Namespace) -> int:
    """多轮对话模式：共享消息历史，持续交互直到 /exit。"""
    from agent.context import estimate_tokens, maybe_compact
    from agent.session import save_session, load_session, export_markdown

    # ── 启动画面 ──
    print(SEAL_NAME)
    print(f"  {DIM}输入 /help 查看命令  |  /exit 退出{RESET}")
    print()

    agent, backend_type, tracer = _build_agent(args)
    system = _build_system()

    # 检查是否有历史会话可恢复
    from agent.session import list_sessions as list_saved
    resumed = False
    all_sessions = list_saved()
    if all_sessions:
        total = len(all_sessions)
        last = all_sessions[0]
        print(f"  {DIM}▸ 检测到 {total} 个历史会话（最近: {last['preview'][:40]}…），输入 /resume 选择恢复{RESET}")
        print()

    messages: list[dict] = [{"role": "system", "content": system}]

    tw = shutil.get_terminal_size().columns
    sep = f"{DIM}{'─' * min(tw, 80)}{RESET}"

    # 后端状态提示
    backend_label = f"{BLUE}DeepSeek{RESET}" if backend_type == "deepseek" else f"{DIM}FakeBackend (离线){RESET}"
    model = "deepseek-v4-flash" if backend_type == "deepseek" else "rule-based"
    print(f"  {DIM}后端: {backend_label}  {DIM}|  模型: {model}{RESET}")
    print(sep)
    print()

    if backend_type == "fake":
        print(f"  {DIM}⚠ FakeBackend 仅匹配少量关键词，请配置 DEEPSEEK_API_KEY 获得完整体验{RESET}")
        print()

    # ── 计划模式状态 ──
    plan_mode: bool = args.plan
    if plan_mode:
        print(f"  {TEAL}▸ 计划模式已启用：每个任务将先生成计划，确认后执行{RESET}")
        print(f"  {DIM}  输入 /plan 切换{RESET}")
        print()

    def _show_tokens(msgs):
        """打印当前 token 估算。"""
        est = estimate_tokens(msgs)
        usage = f"{est} tokens"
        if est > 6000:
            usage += f" {DIM}(建议 /compact){RESET}"
        print(f"  {DIM}上下文:{RESET} {len(msgs)} 条消息  |  ~{usage}")

    def _print_session_list(sessions):
        """打印历史会话列表。"""
        print(f"  {BOLD}历史会话（共 {len(sessions)} 个）{RESET}")
        for i, s in enumerate(sessions):
            n = f"{BLUE}{i+1}{RESET}"
            ts = s.get("timestamp", "")[:16].replace("T", " ")
            cnt = s["message_count"]
            preview = s.get("preview", "")[:60]
            print(f"  {n}  {DIM}{ts}{RESET}  {DIM}{cnt}msgs{RESET}  {preview}")

    while True:
        try:
            user_input = input(_rl_prompt(f"{BLUE}  ⏣{RESET} ")).strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {DIM}自动保存会话...{RESET}")
            save_session(messages, system)
            print(f"  {DIM}再见！{RESET}")
            break

        if not user_input:
            continue

        # ── 内置命令 ──
        if user_input in ("/exit", "/quit", "/q"):
            save_session(messages, system)
            print(f"  {DIM}会话已保存 · 再见！{RESET}")
            # --trace 时显示本次会话的 trace 回放与成本
            if args.trace and tracer and tracer.spans:
                print(f"\n  {BOLD}▸ Trace 回放{RESET}")
                from agent.tracer import replay, cost_report
                replay(tracer)
                print()
                cost_report(tracer)
            break

        if user_input == "/clear":
            messages = [{"role": "system", "content": system}]
            print(f"  {DIM}✓ 上下文已清空{RESET}\n")
            continue

        if user_input == "/resume":
            # /resume 列出所有会话；/resume <N> 恢复第 N 个
            sessions = list_saved()
            if not sessions:
                print(f"  {DIM}（无历史会话可恢复）{RESET}\n")
                continue
            _print_session_list(sessions)
            print(f"  {DIM}输入 /resume <序号> 选择恢复（如 /resume 1）{RESET}\n")
            continue

        if user_input.startswith("/resume "):
            arg = user_input.split(maxsplit=1)[1].strip()
            sessions = list_saved()
            if not sessions:
                print(f"  {DIM}（无历史会话可恢复）{RESET}\n")
                continue
            # 尝试按序号解析
            try:
                idx = int(arg) - 1
                if 0 <= idx < len(sessions):
                    sid = sessions[idx]["session_id"]
                else:
                    print(f"  {DIM}✗ 序号超出范围（1-{len(sessions)}）{RESET}\n")
                    continue
            except ValueError:
                # 非数字：当作 session_id 或 "last"
                if arg == "last" and sessions:
                    sid = sessions[0]["session_id"]
                else:
                    sid = arg
            last = load_session(sid)
            if not last or not last.get("messages"):
                print(f"  {DIM}✗ 会话不存在：{sid}{RESET}\n")
                continue
            old_messages = last["messages"]
            messages = [{"role": "system", "content": system}] + old_messages[1:]
            resumed = True
            preview = last.get("preview", "")[:50]
            print(f"  {DIM}✓ 已恢复 {len(old_messages)} 条消息  |  {preview}…{RESET}")
            _show_tokens(messages)
            print()
            continue

        if user_input == "/compact":
            before = estimate_tokens(messages)
            if before <= 1000:
                print(f"  {DIM}上下文仅 ~{before} tokens，无需压缩{RESET}\n")
                continue
            print(f"  {DIM}压缩前: ~{before} tokens，{len(messages)} 条消息{RESET}")
            try:
                messages = maybe_compact(messages, agent.backend)
                after = estimate_tokens(messages)
                print(f"  {DIM}压缩后: ~{after} tokens，{len(messages)} 条消息{RESET}")
                print(f"  {DIM}✓ 释放约 {before - after} tokens{RESET}\n")
            except Exception as e:
                print(f"  {DIM}⚠ 压缩失败（后端不可用）：{e}{RESET}\n")
            continue

        if user_input == "/tokens":
            _show_tokens(messages)
            print()
            continue

        if user_input.startswith("/save"):
            # /save [path] — 导出对话为 Markdown
            parts = user_input.split(maxsplit=1)
            out = Path(parts[1]) if len(parts) > 1 else Path("session_export.md")
            try:
                export_markdown(messages, out)
                print(f"  {DIM}✓ 已导出到 {out}（{len(messages)} 条消息）{RESET}\n")
            except Exception as e:
                print(f"  {DIM}✗ 导出失败：{e}{RESET}\n")
            continue

        if user_input == "/help":
            print(f"  {BOLD}命令{RESET}")
            print(f"  {DIM}/exit, /quit, /q{RESET}    退出对话（自动保存）")
            print(f"  {DIM}/resume{RESET}             列出历史会话；/resume <N> 选择恢复")
            print(f"  {DIM}/clear{RESET}              清空上下文历史")
            print(f"  {DIM}/compact{RESET}            手动压缩上下文（释放 token）")
            print(f"  {DIM}/tokens{RESET}             查看当前 token 估算")
            print(f"  {DIM}/save [path]{RESET}        导出对话为 Markdown")
            print(f"  {DIM}/mem{RESET}               查看持久记忆")
            print(f"  {DIM}/model{RESET}             查看当前后端信息")
            print(f"  {DIM}/trace{RESET}             查看本次运行的 span 回放 + 成本")
            print(f"  {DIM}/plan{RESET}             切换计划模式（先出计划，确认后执行）")
            print(f"  {DIM}Ctrl+C{RESET}             中断当前回答\n")
            continue

        if user_input == "/trace":
            if not tracer or not tracer.spans:
                print(f"  {DIM}（暂无 trace 数据）{RESET}\n")
            else:
                from agent.tracer import replay, cost_report
                print()
                replay(tracer)
                print()
                cost_report(tracer)
                print()
            continue

        if user_input == "/mem":
            from agent.memory import Memory, KVMemory
            text_mem = Memory("MEMORY.md").recall()
            kv_mem = KVMemory("memory.json").recall()
            if text_mem.strip():
                print(f"  {BOLD}[MEMORY.md]{RESET}\n  {text_mem}")
            if kv_mem.strip():
                print(f"  {BOLD}[memory.json]{RESET}\n  {kv_mem}")
            if not text_mem.strip() and not kv_mem.strip():
                print(f"  {DIM}（暂无记忆）{RESET}")
            print()
            continue

        if user_input == "/model":
            print(f"  {DIM}后端:{RESET} {backend_label}")
            print(f"  {DIM}模型:{RESET} {model}")
            print(f"  {DIM}工作目录:{RESET} {agent.workdir}")
            _show_tokens(messages)
            print()
            continue

        if user_input == "/plan":
            plan_mode = not plan_mode
            status = f"{TEAL}已启用{RESET}" if plan_mode else f"{DIM}已禁用{RESET}"
            print(f"  {DIM}计划模式:{RESET} {status}\n")
            continue

        # ── Skills 按需注入 ──
        from skills.loader import match_skills, load_skills
        skills = load_skills()
        matched = match_skills(user_input, skills)
        if matched:
            extra = ""
            for s in matched:
                extra += f"\n## {s.name}\n{s.body}\n"
            messages[0]["content"] = system + "\n\n# 本轮激活 Skill\n" + extra

        # ── 计划模式：先规划，再执行 ──
        if plan_mode:
            plan_text = _plan_and_confirm(
                agent, user_input,
                auto_approve=args.auto_approve or args.dangerously_skip_permissions
            )
            if plan_text is None:
                print(f"  {DIM}计划被取消，跳过此任务{RESET}\n")
                print(sep)
                print()
                continue
            # 清除旧计划消息，注入新计划
            messages = [m for m in messages
                        if not (m.get("role") == "system"
                                and m.get("content", "").startswith("# 执行计划"))]
            messages.append({"role": "user", "content": user_input})
            messages.insert(1, {
                "role": "system",
                "content": f"# 执行计划（用户已确认，严格按此计划推进）\n\n{plan_text}",
            })
            print(f"  {DIM}Thinking...{RESET}", end="\r")
            try:
                messages, result = agent.chat(messages)
            except KeyboardInterrupt:
                print(f"\r  {DIM}⏎ 已中断{RESET}")
                save_session(messages, system)
                print(sep)
                print()
                continue
            except Exception as e:
                result = f"[错误] Agent 运行异常：{e}"
        else:
            messages.append({"role": "user", "content": user_input})

            # ── 思考中提示 ──
            print(f"  {DIM}Thinking...{RESET}", end="\r")

            try:
                messages, result = agent.chat(messages)
            except KeyboardInterrupt:
                print(f"\r  {DIM}⏎ 已中断{RESET}")
                save_session(messages, system)
                print(sep)
                print()
                continue
            except Exception as e:
                result = f"[错误] Agent 运行异常：{e}"

        # 清除"思考中"并打印结果
        print(f"\r{DIM}──────────────{RESET}")
        print(result)
        print(sep)
        print()

        # 每轮回答后自动保存（防崩溃丢上下文）
        try:
            save_session(messages, system)
        except Exception:
            pass

    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）。留空进入多轮对话模式。")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    p.add_argument("--chat", "-c", action="store_true",
                   help="进入多轮对话模式（无 task 时默认行为）")
    p.add_argument("--auto-approve", action="store_true",
                   help="跳过权限确认，放行 confirm 级工具（写/bash/web_fetch）")
    p.add_argument("--dangerously-skip-permissions", action="store_true",
                   help="跳过 confirm 级权限确认，但 deny 命令依然严格禁止")
    p.add_argument("--workdir", type=Path, default=Path.cwd(),
                   help="工作目录边界（默认当前目录），写入操作不得越界")
    p.add_argument("--trace", action="store_true",
                   help="任务结束后打印 trace 回放报告（span 列表 + token 成本核算）")
    p.add_argument("--plan", action="store_true",
                   help="启用规划模式：先出计划 → 用户确认 → 再执行")
    args = p.parse_args(argv)

    if args.selfcheck:
        return selfcheck()

    # 无任务 或 显式 --chat：进入多轮对话模式
    if args.chat or not args.task:
        return _chat_loop(args)

    # 单次任务模式（兼容旧接口）
    agent, _, tracer = _build_agent(args)
    agent.system_prompt = _build_system(args.task)

    if args.plan:
        plan_text = _plan_and_confirm(
            agent, args.task,
            auto_approve=args.auto_approve or args.dangerously_skip_permissions
        )
        if plan_text is None:
            print(f"  {DIM}计划被取消，未执行任何操作{RESET}")
            return 0
        # 注入计划到 system_prompt
        agent.system_prompt += f"\n\n# 执行计划（用户已确认，严格按此计划推进）\n\n{plan_text}"

    try:
        result = agent.run(args.task)
    except Exception as e:
        result = f"[错误] Agent 运行异常：{e}"
    print(result)
    # --trace 时显示 trace 回放 + 成本
    if args.trace and tracer and tracer.spans:
        from agent.tracer import replay, cost_report
        print()
        print(f"{'─'*50}")
        print("Trace 回放")
        print(f"{'─'*50}")
        replay(tracer)
        print()
        cost_report(tracer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
