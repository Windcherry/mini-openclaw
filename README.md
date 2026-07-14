# mini-OpenClaw

> 一个 Claude Code 风格的 CLI 智能体，专为**代码库导读（Repo Guide）**定制。
> 10 天从骨架到完整 Agent 框架，涵盖 ReAct 主循环、24 个工具、MCP 协议、Skills 技能系统、安全防线。

```
  ╭────────────────────────────╮
  │  mini-OpenClaw             │
  │  Repo Guide · 代码库导读    │
  ╰────────────────────────────╯
```

## 这是什么

mini-OpenClaw 是一个命令行 Agent 框架，核心是 **ReAct 主循环**调用 **DeepSeek API 后端**，模型输出**工具调用**（read/write/bash/edit/grep/…），主循环执行工具并把结果喂回模型，如此循环直到任务完成。

在此基础上叠加了 **MCP**（可插拔外部工具）、**Skills**（领域知识按需加载）、**权限分层**（allow/confirm/deny）、**沙箱执行**（bwrap）、**任务规划**（TodoList 状态机）和**长程控制**（绕圈/停滞检测）。

```
你的任务 ──► [agent/cli.py] ──► [agent/loop.py: ReAct 主循环]
                  │                    ↑        │
                  │      [agent/permissions.py]  │ 权限关卡
                  │                    │        ▼
                  │              [backend/client.py]
                  │              DeepSeek API 后端
                  │                    │
                  │      [tools/] ◄────┘ 执行工具调用
                  │      read · write · bash · edit · grep · glob
                  │      web_fetch · todo_write · update_todo · remember
                  │      repo_structure · mermaid_diagram · static_scan · …
                  │      git_clone · git_bisect · git_blame · …
                  │
                  ├── MCP (mcp/) ── 可插拔外部工具
                  ├── Skills (skills/) ── 领域知识按需注入
                  ├── Memory ── 持久记忆
                  └── Session ── 会话持久化
```

## 快速开始

```bash
# 环境
conda create -n openclaw python=3.11 && conda activate openclaw
pip install -r requirements.txt

# 骨架自检（无需 API Key）
python -m agent.cli --selfcheck

# 配置 DeepSeek API Key 后，单次任务
export DEEPSEEK_API_KEY="sk-…"
python -m agent.cli "创建 hello.py 并运行输出当前时间"

# 多轮对话模式
python -m agent.cli --chat
```

**多轮对话命令：**

| 命令 | 功能 |
|------|------|
| `/help` | 查看所有命令 |
| `/resume` | 列出历史会话；`/resume 1` 恢复 |
| `/compact` | 手动压缩上下文 |
| `/tokens` | 查看当前 token 估算 |
| `/save [path]` | 导出对话为 Markdown |
| `/mem` | 查看持久记忆 |
| `/model` | 查看当前后端信息 |
| `/exit` | 退出（自动保存会话） |
| `Ctrl+C` | 中断当前回答 |

聊天框支持 ↑↓ 历史记录、←→ 光标移动，历史文件持久化在 `~/.mini-openclaw_history`。

## 功能特性

### 24 个内置工具

| 类别 | 工具 | 数量 |
|------|------|------|
| 基础 | `read`, `write`, `bash` | 3 |
| 搜索/编辑 | `edit`, `grep`, `glob` | 3 |
| Web/任务/记忆 | `web_fetch`, `todo_write`, `update_todo`, `remember` | 4 |
| 代码分析 | `repo_structure`, `mermaid_diagram`, `static_scan`, `code_analyze`, `generate_diff`, `code_search`, `dep_graph`, `test_runner` | 8 |
| Git | `git_clone`, `git_bisect_start`, `git_bisect_step`, `git_bisect_reset`, `git_blame`, `git_show_commit` | 6 |

### 安全防线

```
模型对齐 → 注入防护 → 权限分层 → 破坏性检测 → 沙箱执行
```

| 层级 | 机制 | 文件 |
|------|------|------|
| 0 | 模型安全对齐 | (DeepSeek 模型) |
| 1 | 注入防护 | `tools/guard.py` — `<external>` 边界标记 + 外发域名白名单 |
| 2 | 权限分层 | `agent/permissions.py` — allow / confirm / deny 三级 |
| 3 | 破坏性检测 | `agent/permissions.py` — fork 炸弹/mkfs/dd 永远拒绝；rm/chmod 仅在目标为系统路径时拒绝 |
| 4 | 沙箱 | `tools/shell.py` — bwrap 只读根文件系统 + 禁网；无 bwrap 时降级到黑名单 |

**权限模型：**

```
bash 命令
  ├─ 无条件 deny：    :(){ 、mkfs、dd if=、> /dev/sd
  ├─ 系统路径 deny：   rm -rf /、chmod -R /usr、rm ~  等
  ├─ 安全 auto-allow： ls、cat、echo、pwd、head、ps …（纯只读）
  └─ 其余 confirm：    pip install、git push、rm ./folder …
```

`--auto-approve` 可跳过 confirm，但 deny 不可绕过。

### 任务规划

`agent/planning.py` 提供 `TodoList` 状态机，Agent 把大任务拆成有序子任务：

```
[x] 1 盘点项目结构
[x] 2 分析依赖关系
[~] 3 生成架构图         ← 进行中
[ ] 4 编写 README
[!] 5 运行测试 — 缺少 pytest（已重试 3 次）
```

- 状态流转：`pending → in_progress → completed / blocked`
- 每轮自动注入当前进度到上下文，防止 Agent 跑偏
- `LoopDetector`：连续重复调用同工具 or 多步无进展 → 警告

### 错误恢复

- **瞬时错误**（网络/超时/限流）→ 指数退避重试（最多 3 次）
- **永久错误**（文件不存在/语法错误）→ 不重试，引导模型重规划
- **重试耗尽** → 标记子任务 blocked，先推进其他任务
- **HTTP 错误**：401/429/5xx 分别给出明确提示

### MCP 协议

`mcp/client.py` 实现 stdio + JSON-RPC MCP 客户端，Agent 可接入任何 MCP Server 扩展工具集。

```bash
# 内置 echo server 测试
python -m mcp.echo_server  # 独立运行
```

### Skills 技能系统

`skills/loader.py` 实现三级渐进加载：

1. **元数据**（始终在上下文）— 技能名称 + 描述
2. **完整流程**（任务关键词命中时注入）— SKILL.md 正文
3. **配套资源**（按需加载）— scripts/、references/、assets/

内置 `repo-onboarding` Skill：代码库结构盘点、依赖检索、文件推断、动态验证、README 生成。

### 会话与记忆

- **会话持久化**：自动保存/恢复/导出 Markdown，支持多会话管理
- **持久记忆**：`Memory`（文本） + `KVMemory`（JSON KV），跨会话注入 system prompt

## 红队测试

```bash
python -m security.redteam --backend deepseek --output security/report.md
```

覆盖 4 个攻击面 × 多种测试用例，支持模型推理层 + 工具层双层检测。

## 项目结构

```
mini-openclaw/
├── agent/
│   ├── cli.py           # CLI 入口，接线 + readline 设置
│   ├── loop.py          # ReAct 主循环（心脏）
│   ├── permissions.py   # 权限分层（allow/confirm/deny）
│   ├── planning.py      # TodoList 状态机 + LoopDetector
│   ├── prompts.py       # 系统提示词
│   ├── context.py       # Token 预算 + 上下文压缩
│   ├── memory.py        # 持久记忆
│   └── session.py       # 会话持久化
├── backend/
│   ├── client.py        # DeepSeek API 客户端
│   └── fake_backend.py  # 规则假后端（无需 API Key）
├── tools/
│   ├── base.py          # Tool + ToolRegistry
│   ├── fs.py            # read / write
│   ├── shell.py         # bash（bwrap 沙箱）
│   ├── more_tools.py    # edit / grep / glob / web_fetch / todo / remember
│   ├── guard.py         # 注入防护
│   ├── code_analysis.py # 8 个代码分析工具
│   └── git_ops.py       # 6 个 Git 工具
├── mcp/
│   ├── client.py        # MCP stdio 客户端
│   └── echo_server.py   # 最小 echo MCP server
├── skills/
│   ├── loader.py        # Skills 加载与匹配
│   └── repo-onboarding/ # 代码库导读 Skill
├── prompt/
│   └── render.py        # ChatML 渲染 + tool call 解析
├── eval/
│   ├── tasks.py         # 评测任务集
│   ├── metrics.py       # 指标计算
│   ├── judge.py         # LLM 裁判
│   ├── tracer.py        # 轨迹记录
│   └── ablation.py      # 消融实验
├── security/
│   └── redteam.py       # 红队安全测试
├── CLAUDE.md            # Claude Code 工作指南
├── README.md
└── requirements.txt
```

## License

MIT
