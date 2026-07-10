# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

mini-OpenClaw is a 10-day educational project to build a Claude Code-style CLI agent from scratch. The agent has a **ReAct main loop** that calls a **DeepSeek API backend**, which returns **tool calls** (read/write/bash/…). The loop executes tools, feeds results back to the model, and repeats until the task is done. MCP (pluggable external tools) and Skills (domain knowledge packs) are layered on top.

## Commands

```bash
# Environment setup
conda create -n openclaw python=3.11 && conda activate openclaw
pip install -r requirements.txt

# Skeleton self-check (works Day 1, no API key needed)
python -m agent.cli --selfcheck

# Show CLI help
python -m agent.cli --help

# Run a task — DeepSeekBackend if DEEPSEEK_API_KEY is set, FakeBackend otherwise
python -m agent.cli "创建 hello.py 并运行输出当前时间"

# Quick backend smoke test (Day 2; requires DEEPSEEK_API_KEY)
python demo_m2.py

# Find all TODO markers across the codebase
grep -rn "TODO\[Day" .
```

## Architecture

```
User task → [agent/cli.py] → [agent/loop.py: AgentLoop] → [backend/client.py: DeepSeekBackend]
                                  ↑        │
                                  │        ▼ model outputs tool_calls
                                  │   [tools/] execute tool.run()
                                  │        │
                                  └────────┘ tool result fed back as observation
```

### Internal Message Format

Every message in the loop is a dict with at least `role` and `content`:

- **system**: `{role: "system", content: str}` — injected once at position 0
- **user**: `{role: "user", content: str}` — the task, then later observations presented as user context
- **assistant**: `{role: "assistant", content: str, tool_calls: [{id, name, arguments}]}` — model output
- **tool**: `{role: "tool", name: str, tool_call_id: str, content: str}` — tool execution result

`backend/client.py` converts between this internal format and OpenAI's wire format:
- **Internal → wire** (`_to_openai_messages`): tool messages get `tool_call_id`; assistant tool_calls get JSON-stringified `arguments` and a `function` wrapper.
- **Wire → internal** (`_normalize`): `arguments` strings are `json.loads`-parsed back to dicts; the `function` wrapper is stripped.

### tool_call_id Lifecycle

The loop in `agent/loop.py` assigns an `id` to each tool call from the model's response. This `id` flows through `backend/client.py`'s normalization (as OpenAI's `tool_call_id` on `role=tool` messages). It's required by the OpenAI-compatible protocol — without it, the API rejects assistant→tool message sequences.

### Backend Fallback

`DeepSeekBackend.__init__()` raises `RuntimeError` when `DEEPSEEK_API_KEY` is missing. `agent/cli.py` catches this and falls back to `FakeBackend` — a rule-based stub that matches a few keywords to emit a dummy tool call, then returns a final message. The pipeline stays runnable without an API key.

### Module Map

| Module | Purpose | Status |
|--------|---------|--------|
| `agent/cli.py` | CLI entry point; parses args, wires backend + registry + loop | Skeleton done; auto-falls back to FakeBackend |
| `agent/loop.py` | ReAct loop: calls backend, executes tool_calls, injects observations, max_turns=20 guard | Loop skeleton complete; tool dispatch works but tools are `NotImplementedError` |
| `agent/prompts.py` | `SYSTEM_PROMPT` — the system message that defines agent behavior | Draft exists; TODO[Day5][Day7] to refine |
| `agent/context.py` | Token budget estimation, sliding-window compaction, observation truncation | Skeleton only; TODO[Day7] |
| `backend/client.py` | DeepSeek API client (OpenAI-compatible); sync `httpx.Client`, normalizes messages | **Complete** |
| `backend/fake_backend.py` | Rule-based fake model: detects tool-result messages → final answer; else emits a dummy tool call if keywords match | **Complete** |
| `backend/server.py` | Placeholder retained for optional middleware wrapping (retry/logging/rate-limiting proxy) | Deprecated; use `client.py` directly |
| `tools/base.py` | `Tool` dataclass + `ToolRegistry` + `build_default_registry()` | Registry done; tool registration commented out (TODO[Day5–7]) |
| `tools/fs.py` | `read` / `write` tools | Signatures defined; TODO[Day5] |
| `tools/shell.py` | `bash` tool (timeout default 30s) | Signature defined; TODO[Day5]; TODO[Day10] sandbox |
| `tools/more_tools.py` | `edit` / `grep` / `glob` (Day6) + `web_fetch` / `task_list` (Day7) | Signatures defined; all TODO |
| `prompt/render.py` | `render_prompt()`: messages + tools → single text string; `parse_tool_calls()`: extract tool calls from model output | Skeleton; TODO[Day3] |
| `mcp/client.py` | MCP stdio+JSON-RPC client; `register_mcp_tools()` merges MCP tools into built-in registry with `mcp__` prefix | Skeleton; TODO[Day8] |
| `mcp/echo_server.py` | Minimal MCP echo server (stdio JSON-RPC loop, `echo` tool) | **Complete** |
| `skills/loader.py` | Scans `skills/*/SKILL.md`, parses YAML frontmatter, generates catalog for system prompt | Skeleton; TODO[Day9] |
| `eval/tasks.py` | Tool-call test cases + end-to-end task definitions | Format examples; TODO to populate |
| `eval/metrics.py` | Tool-call quality metrics: JSON validity, tool choice accuracy, arg accuracy | Skeleton; TODO[Day7] |

### Key Design Decisions

- **Backend contract**: `chat(messages, tools) → {"role": "assistant", "content": str, "tool_calls": [{name, arguments}, ...]}`. Both `DeepSeekBackend` and `FakeBackend` implement this.
- **DeepSeek API** uses OpenAI-compatible protocol. `backend/client.py` normalizes between the internal message format (above) and the OpenAI wire format — the rest of the codebase never touches OpenAI-format messages.
- **Tool execution is text-in, text-out**: `Tool.run(**arguments) → str`. The model generates a tool_call JSON; the loop parses it, finds the matching Tool, calls `.run()`, and injects the result string as a `role=tool` observation.
- **No local model deployment**: The brain is always a cloud API (DeepSeek). The `prompt/render.py` module exists for Day 3's educational deep-dive into tokenization and prompt templates.
- **Tool naming convention**: Built-in tools use bare names (`read`, `bash`). MCP tools get an `mcp__` prefix to avoid collisions.
- **Skills vs Tools**: A Tool is a single function call. A Skill is a `SKILL.md` file (YAML frontmatter + markdown body) that encodes domain knowledge and multi-step procedures, loaded on-demand into the system prompt.

### Milestones

- **v1 (Day 6)**: End-to-end usable — agent can complete "create hello.py and run it"
- **v3 (Day 9)**: MCP + Skills integration
- **final (Day 10)**: Security layer (sandbox, permissions), Demo Day

### Environment Variables

- `DEEPSEEK_API_KEY` — required for real model; `DeepSeekBackend.__init__()` raises RuntimeError if absent
- `DEEPSEEK_BASE_URL` — default `https://api.deepseek.com`
- `DEEPSEEK_MODEL` — default `deepseek-chat`
