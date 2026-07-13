# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

mini-OpenClaw is a 10-day educational project to build a Claude Code-style CLI agent from scratch, specialized as a **"代码库导读 (Repo Guide)"** agent. The agent has a **ReAct main loop** that calls a **DeepSeek API backend**, which returns **tool calls** (read/write/bash/…). The loop executes tools, feeds results back to the model, and repeats until the task is done. MCP (pluggable external tools) and Skills (domain knowledge packs) are layered on top.

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
# With auto-approve (skip permission confirmations for write/bash/web_fetch)
python -m agent.cli --auto-approve "用 bash 列出当前目录文件"
# Specify working directory boundary
python -m agent.cli --workdir /tmp/test "写文件到当前目录"

# Red team security testing
python -m security.redteam --backend deepseek --output security/report.md
```

## Architecture

```
                    ┌── Skills (catalog + on-demand body injection)
                    │
User task → [agent/cli.py] → [agent/loop.py: AgentLoop] → [backend/client.py: DeepSeekBackend]
                 │                    ↑        │
                 │                    │        ▼ model outputs tool_calls
                 │        [agent/permissions.py]  ← Day10 权限关卡
                 │                    │        │
                 │                    │   [tools/] execute tool.run()
                 │                    │        │  (guard: <external> + allowlist)
                 │    ┌── MCP server ─┘        │
                 │    │ (stdio subprocess)      │
                 │    │                        │
                 └────┘                        │
                   tool result fed back as observation ←──┘
```

### Startup Pipeline (agent/cli.py)

```
build_default_registry()     → 22 built-in tools
    ↓
load_skills() + match_skills → catalog in system prompt, matched body injected
    ↓
MCPClient.start() + register_mcp_tools → mcp__echo merged into registry
    ↓
DeepSeekBackend() (or FakeBackend fallback)
    ↓
AgentLoop(backend, registry, system_prompt)
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
| `agent/cli.py` | CLI entry point; wires backend + registry + MCP + Skills + loop | **Complete** |
| `agent/loop.py` | ReAct loop: calls backend, permissions check before tool execution, error recovery, context compaction, max_turns=40 guard | **Complete** (Day10) |
| `agent/permissions.py` | `check()` — 3-tier permission model (READONLY allow / WRITE workdir-bound confirm/deny / EXEC confirm) + destructive command patterns | **Complete** (Day10) |
| `agent/prompts.py` | `SYSTEM_PROMPT` — the system message that defines agent behavior | **Complete** (Day5) |
| `agent/context.py` | Token budget estimation (chars/4 + tool_call fields), `maybe_compact` (keep last K=4 rounds + summary), `truncate_observation` | **Complete** (Day7) |
| `backend/client.py` | DeepSeek API client (OpenAI-compatible); sync `httpx.Client`, normalizes messages | **Complete** |
| `backend/fake_backend.py` | Rule-based fake model: detects tool-result messages → final answer; else emits a dummy tool call if keywords match | **Complete** |
| `backend/server.py` | Placeholder retained for optional middleware wrapping | Deprecated; use `client.py` directly |
| `tools/base.py` | `Tool` dataclass + `ToolRegistry` + `build_default_registry()` | **Complete**; 22 tools registered |
| `tools/fs.py` | `read` / `write` tools — read with line numbers + truncation + `<external>` boundary wrapping; write with auto-mkdir; path boundary enforced by `agent/permissions.py` | **Complete** (Day10) |
| `tools/shell.py` | `bash` tool — bwrap sandbox (ro-bind root, writable cwd, unshare-net) with deny-list fallback; timeout + stdout/stderr/returncode | **Complete** (Day10) |
| `tools/more_tools.py` | `edit` / `grep` / `glob` / `web_fetch` (domain allowlist + `<external>` wrapping) / `task_list` | **Complete** (Day10) |
| `tools/guard.py` | `wrap_external()` — external content boundary isolation; `check_host()` — outbound domain allowlist for web_fetch | **Complete** (Day10) |
| `tools/code_analysis.py` | 8 code analysis tools: repo_structure, mermaid_diagram, static_scan, code_analyze, generate_diff, code_search, dep_graph, test_runner | **Complete** |
| `tools/git_ops.py` | 6 git tools: clone, bisect (start/step/reset), blame, show_commit | **Complete** |
| `prompt/render.py` | `render_prompt()` + `parse_tool_calls()` | Skeleton; TODO[Day3] |
| `mcp/client.py` | MCP stdio+JSON-RPC client — `start()` handshake, `_rpc()`/`_notify()`, `list_tools()`/`call_tool()`, robustness for startup failure/empty readline/error field | **Complete** (Day8) |
| `mcp/echo_server.py` | Minimal MCP echo server (stdio JSON-RPC loop, `echo` tool) | **Complete** |
| `skills/loader.py` | `parse_skill_md()` (manual YAML frontmatter), `load_skills()`, `skills_catalog()`, `match_skills()` (3-strategy keyword recall with stop-words + bigram) | **Complete** (Day9) |
| `skills/repo-onboarding/` | **代码库导读 Skill**: SKILL.md (5 workflows) + `scripts/detect_project.py` + `references/` (directory/naming conventions) + `assets/GUIDE_TEMPLATE.md` | **Complete** (Day9) |
| `eval/tasks.py` | 13 trajectory-based Task definitions with programmatic check functions | **Complete** (Day3) |
| `eval/metrics.py` | 4 metrics (success_rate, step_count, token_count, json_valid_rate) + 15 SAMPLE_RECORDS | **Complete** (Day3) |
| `eval/judge.py` | LLM-as-judge with 6 domain-specific rubrics | **Complete** (Day3) |
| `eval/tracer.py` | JSONL trajectory recorder (Tracer, replay, load_trajectory) | **Complete** (Day3) |
| `eval/ablation.py` | Ablation study tooling + system-prompt ablation groups | **Complete** (Day3) |
| `security/redteam.py` | Red team security testing: 4 attack surfaces × 6 test cases, dual-mode (model reasoning + tool-layer direct), auto-generated markdown report | **Complete** (Day10) |
| `demo/inject.html` | Prompt injection test fixture — hidden malicious instructions in HTML comments + display:none paragraph | **Complete** (Day10) |

### Key Design Decisions

- **Backend contract**: `chat(messages, tools) → {"role": "assistant", "content": str, "tool_calls": [{name, arguments}, ...]}`. Both `DeepSeekBackend` and `FakeBackend` implement this.
- **DeepSeek API** uses OpenAI-compatible protocol. `backend/client.py` normalizes between the internal message format and the OpenAI wire format — the rest of the codebase never touches OpenAI-format messages.
- **Tool execution is text-in, text-out**: `Tool.run(**arguments) → str`. The model generates a tool_call JSON; the loop parses it, finds the matching Tool, calls `.run()`, and injects the result string as a `role=tool` observation.
- **No local model deployment**: The brain is always a cloud API (DeepSeek).
- **Tool naming convention**: Built-in tools use bare names (`read`, `bash`). MCP tools get an `mcp__` prefix to avoid collisions.
- **Skills vs Tools**: A Tool is a single function call. A Skill is a `SKILL.md` file (YAML frontmatter + Markdown body) with optional `scripts/`, `references/`, and `assets/`. Skills are loaded on-demand via keyword matching against the task description.
- **MCP transparency**: MCP tools are registered into the same `ToolRegistry` as built-in tools. `AgentLoop` treats them identically — the model doesn't distinguish between built-in and MCP tools.
- **Progressive disclosure**: Skills use 3-level loading — metadata (always in context, ~100 words), body (injected when task matches keywords, <5k words), bundled resources (loaded on demand by Claude).
- **Permission model**: 3-tier `check()` inserted between tool call parsing and execution. `deny` is absolute (even `--auto-approve` cannot override). Destructive bash commands (`rm -rf`, `mkfs`, etc.) are hard-denied.
- **Defense-in-depth**: 5-layer security — model alignment → injection guard → permissions → destructive detection → sandbox. No single bypass compromises all layers.

### Milestones

- **v1 (Day 6)**: ✅ 6 core tools (read/write/bash/edit/grep/glob) + 14 domain tools = 20 tools. Agent completes "create hello.py and run it" end-to-end.
- **v2 (Day 7)**: ✅ web_fetch + task_list + error recovery (try/except in loop) + context compaction (maybe_compact with K=4 sliding window + backend-driven summarization)
- **v3 (Day 9)**: ✅ MCP stdio client (handshake + tools/list + tools/call, robustness hardened) + Skills system (parse_skill_md + match_skills 3-strategy recall + repo-onboarding skill with scripts/references/assets)
- **final (Day 10)**: ✅ Security layer — 3-tier permissions (read allow / write workdir-bound / exec confirm), shell sandbox (bwrap + deny-list), prompt injection guard (`<external>` boundary + outbound allowlist), destructive command detection, red team testing (6/6 intercepted), HTTP error recovery

### Tool Inventory (22 tools, Day 10)

| Category | Tools | Count |
|----------|-------|-------|
| Base (Day5) | read, write, bash | 3 |
| Search/Edit (Day6) | edit, grep, glob | 3 |
| Web/Tasks (Day7) | web_fetch, task_list | 2 |
| Code Analysis | repo_structure, mermaid_diagram, static_scan, code_analyze, generate_diff, code_search, dep_graph, test_runner | 8 |
| Git | git_clone, git_bisect_start, git_bisect_step, git_bisect_reset, git_blame, git_show_commit | 6 |
| MCP (Day8) | mcp__echo (from echo_server.py) | 1+ |

### Skills (Day 9)

| Skill | Description | Resources |
|-------|-------------|-----------|
| `repo-onboarding` | 代码库导读：结构盘点、依赖检索、文件推断、动态验证、物理重排布、README 生成 | `scripts/detect_project.py`, `references/directory-conventions.md`, `references/naming-conventions.md`, `assets/GUIDE_TEMPLATE.md` |

Skill loading flow:
1. `load_skills()` scans `skills/*/SKILL.md`, parses YAML frontmatter
2. `skills_catalog()` renders name+description list → always appended to system prompt
3. `match_skills(task, skills)` uses 3-strategy keyword matching (name hit → description keyword → bigram with stop-words) → matched skill body injected into system prompt

### Context Management (Day 7)

- **Token estimation**: `estimate_tokens(messages)` — chars/4, plus tool_call fields (arguments, id, name, tool_call_id)
- **Compaction**: `maybe_compact(messages, backend, budget=6000)` — when tokens exceed budget, keep system[0] + last K=4 assistant rounds, summarize middle history via backend into a system备忘
- **Truncation**: `truncate_observation(text, max_chars=4000)` — tool results truncated with `...[已截断，共 N 字符]` marker

### Error Recovery (Day 7)

Tool execution in `agent/loop.py` is wrapped in try/except — exceptions are caught and converted to observation text fed back to the model for self-correction. Unknown tools produce `"错误：未知工具 {name}"`.

### Security (Day 10)

**Defense-in-depth — 5 layers from reasoning to sandbox:**

| Layer | Mechanism | File | Verdicts |
|-------|-----------|------|----------|
| 0 | Model safety alignment | (DeepSeek model) | Refuses obviously malicious tasks at reasoning level |
| 1 | Prompt injection guard | `tools/guard.py` | `<external>` boundary markers + outbound domain allowlist |
| 2 | Permission tiers | `agent/permissions.py` | `allow` (read) / `confirm` (write/exec) / `deny` (destructive) |
| 3 | Destructive command detection | `agent/permissions.py` | `rm -rf`, `chmod -R`, `mkfs`, etc. → always `deny` |
| 4 | Shell sandbox | `tools/shell.py` | bwrap (ro-bind `/`, bind cwd, unshare-net) + DENY_PATTERNS fallback |

**Permission model (`agent/permissions.py:check()`):**

- `READONLY = {read, grep, glob}` → `allow`
- `WRITE = {write, edit}` → `confirm` if path in workdir, else `deny`
- `EXEC = {bash, web_fetch}` → `confirm`; bash additionally checks `DESTRUCTIVE` patterns → `deny`
- Unknown tools (MCP) → `confirm` (conservative)

**CLI flags:**

- `--auto-approve` — skip `confirm` verdicts (but `deny` is never skippable)
- `--workdir PATH` — set working directory boundary (default: cwd)

**Red team results (6 cases, DeepSeek backend):**

All 4 attack surfaces + 2 disguised bypass tasks intercepted at reasoning layer. 7/7 tool-layer direct tests passed (permissions deny, sandbox deny-list, outbound allowlist, `<external>` wrapping).

### Environment Variables

- `DEEPSEEK_API_KEY` — required for real model; `DeepSeekBackend.__init__()` raises RuntimeError if absent
- `DEEPSEEK_BASE_URL` — default `https://api.deepseek.com`
- `DEEPSEEK_MODEL` — default `deepseek-chat`
