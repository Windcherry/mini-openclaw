"""命令行入口。

用法：
  python -m agent.cli --selfcheck          # Day1：自检骨架是否装好
  python -m agent.cli "创建 hello.py 并运行"  # Day5 起：真正跑任务（v1 在 Day6）
"""
from __future__ import annotations
import argparse
import sys

from tools.base import build_default_registry
from agent.prompts import SYSTEM_PROMPT


def selfcheck() -> int:
    print("== mini-OpenClaw 自检 ==")
    ok = True
    try:
        reg = build_default_registry()
        print(f"[ok] 工具注册表加载成功，当前内置工具数：{len(reg)}（Day5 起会变多）")
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
        print("[ok] 主循环模块可导入（Day5 实现 run 逻辑）")
    except Exception as e:  # noqa
        print(f"[FAIL] 主循环：{e}"); ok = False

    print("== 自检", "通过 ✅" if ok else "未通过 ❌", "==")
    print("\n下一步：按 dayNN 的 lab-guide 填 # TODO 标记。")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    args = p.parse_args(argv)

    if args.selfcheck or not args.task:
        return selfcheck()

    from agent.loop import AgentLoop
    reg = build_default_registry()

    # Skills：catalog 始终可见，匹配的 body 按需注入
    from skills.loader import load_skills, skills_catalog, match_skills
    skills = load_skills()
    system = SYSTEM_PROMPT + "\n\n# 可用 Skills（相关时按其流程执行）\n" + skills_catalog(skills)
    matched = match_skills(args.task, skills)
    if matched:
        system += "\n\n# 已激活 Skill（任务命中，注入完整流程）\n"
        for s in matched:
            system += f"\n## {s.name}\n{s.body}\n"

    # MCP：stdio 子进程接入外部工具，透明合并到 registry
    from mcp.client import MCPClient, register_mcp_tools
    try:
        mcp = MCPClient(["python", "mcp/echo_server.py"])
        mcp.start()
        register_mcp_tools(reg, mcp)
    except Exception as e:
        print(f"[提示] MCP 未接入（{e}），仅用内置工具。")

    # 后端：DeepSeek API 优先，未配 key 时回退 FakeBackend
    try:
        from backend.client import DeepSeekBackend
        backend = DeepSeekBackend()
    except Exception as e:
        from backend.fake_backend import FakeBackend
        print(f"[提示] 未启用真后端（{e}），回退 FakeBackend。")
        backend = FakeBackend()
    agent = AgentLoop(backend, reg, system)
    print(agent.run(args.task))
    return 0


if __name__ == "__main__":
    sys.exit(main())
