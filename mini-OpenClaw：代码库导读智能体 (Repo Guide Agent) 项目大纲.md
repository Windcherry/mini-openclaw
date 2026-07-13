# mini-OpenClaw：代码库导读智能体 (Repo Guide Agent) 项目大纲

## 一、 实现目标

本项目旨在从零构建一个轻量级、类 Claude Code 的命令行智能体框架，并深度定制为**“代码库导读” (Repo Guide)** 领域的专属 Agent。核心实现目标包括：

1. **全面盘点现状与摸清骨架**：
   - 使用 `glob` 或 `bash` 工具查找并扫描目录下的所有核心文件（重点关注 `.py`, `.md`, `requirements.txt` 等），了解整体目录树和当前的混乱状况。
   - 针对命名极其模糊的文件（如 `test1.py`, `a.txt`），使用 `read_file` 工具读取文件内容，准确推断它的真实业务功能。

2. **依赖与外部知识检索**：
   - 使用 `read`（或 `read_file`）工具读取依赖配置文件。
   - 如果遇到生僻的核心依赖库，使用 `web_fetch` 工具去互联网抓取该库的官方说明，以准确理解其作用。

3. **定位核心模块与动态验证**：
   - 使用 `grep` 工具在核心文件中搜索关键定义（如 `def main`, `class `, `TODO` 等），快速定位代码的入口点和关键逻辑。
   - 视情况使用 `bash` 工具运行简单的自检命令（例如查看帮助文档 `python main.py --help`），捕获报错信息以动态确认代码的可用性。

4. **重排布与重命名**：
   - 结合文件内容，设计一个清晰、标准化的目录结构（如源码放入 `src/`，文档放入 `docs/`，测试放入 `tests/`）及统一的命名规范（如 `snake_case`）。
   - 使用 `bash` 工具执行 `mkdir -p` 创建规划好的新目录。
   - 使用 `bash` 工具执行 `mv` 命令，将文件移动到新目录并赋予规范合理的新名字。注意：移动和重命名时必须谨慎，一次可结合多条命令执行，切勿误删文件。

5. **生成全面的 README 并输出报告**：
   - 如果进行了目录重排布，务必利用掌握的最新仓库结构，使用 `write_file` 工具在根目录生成（或覆盖）一份直观详尽的 `README.md`。
   - 最终，无论是否重构，都要向用户输出一份结构清晰的模块导读说明。报告必须采用以下 Markdown 模板格式：

------

## 二、 整体架构

系统采用自底向上的高内聚、低耦合设计，整体划分为四大核心层级：

### 1. LLM 交互与多模态抽象层 (`backend/`)

作为 Agent 的“大脑接口”，该层屏蔽了底层 HTTP 请求细节与服务商差异，业务代码只依赖统一入口 `chat()`。

- **API 连通与重试机制**：处理统一的鉴权（如通过 `DEEPSEEK_API_KEY`）、并发控制，以及针对 4xx（如鉴权失败）与 5xx（服务端抖动）的精准分类退避重试。
- **多模态视觉通道**：实现了 `image_block`，将外部图像（如架构图、代码截图）转化为 Base64 编码，无缝嵌入结构化消息发送给视觉大模型。

### 2. 主循环与上下文流控层 (`agent/`)

- **状态机引擎 (`loop.py`)**：编排 `ReAct`（推理-行动）循环。负责组装请求发给模型，拦截其输出的 `tool_calls`，将任务分发执行后以 `role="tool"` 附带 `tool_call_id` 回填至历史记录，驱动对话不断向前。
- **上下文压缩管理器 (`context.py`)**：实现了 `maybe_compact` 和长文本截断 `truncate_observation`。当历史 Token 逼近预算时，保留最新的 K 轮对话，并强制模型将中段历史提炼为保留“任务目标、已完成步骤、关键发现”的核心备忘录，根治长程任务的失忆问题。
- **行为约束协议 (`prompts.py`)**：内置全局 `SYSTEM_PROMPT`，规定其命令行助手的身份、工具使用规范及安全纪律（如遇错重试、谨慎覆盖等）。

### 3. 本地与扩展工具执行层 (`tools/` & `mcp/` & `skills/`)

采用 JSON Schema 定义严格的工具调用契约。

- 本地物理工具 (`tools/`)
  - `fs.py`：支持超长文本截断提示并附加行号的 `read` 工具，以及覆盖写入的 `write` 工具。
  - `shell.py`：具有超时控制的受控 `bash` 终端工具。
  - `more_tools.py`：底层依托 `ripgrep` 的超高速全文检索 `grep`、文件拓扑探索 `glob`、精准 AST 无损篡改 `edit`，以及网络爬取 `web_fetch`。
- **外部联邦工具 (`mcp/`)**：实现了极简的 JSON-RPC 客户端，能够通过 `stdio` 协议握手，将外部 MCP Server（如官方文件系统 Server）的工具透明化拉取并并入 Agent 主循环。
- **动态领域技能引擎 (`skills/`)**：`loader.py` 通过解析 `SKILL.md` 的 YAML 前置元数据（Frontmatter），在用户需求命中 `description` 关键字时，将代码导读的垂直领域 SOP 按需注入大模型。

### 4. 零侵入评估台基座 (`eval/`)

贯彻“先记录、后评估”理念，从不上线盲测。

- **轨迹追踪 (`tracer.py`)**：记录智能体每一步行动，输出结构化 JSONL 轨迹，支持全过程复盘回放。
- **多维指标度量 (`metrics.py` & `tasks.py`)**：不只看“最终对错”，全面量化成功率、平均步数、Token 开销（成本）以及 JSON Schema 合法率。
- **LLM-as-judge (`judge.py`)**：对于开放式导读报告，调用模型作为严格裁判，依据预设 Rubric 量表给出 1-5 分的自动化质检打分。

------

## 三、 项目特点

1. **高鲁棒性的自愈机制 (Self-Healing)** 项目底层的工具执行完全由 `try-except` 包裹。无论是读取不存在的文件产生 `FileNotFoundError`，或是模型输出破损的调用参数抛出 `JSONDecodeError`，抑或是终端命令执行报错（如返回非 0 状态码或 `stderr` 有输出），系统都不会崩溃。这些报错会被原封不动地转化为观测结果（Observation）回喂给模型，让模型像人类程序员一样分析 Error Log 并自动修正参数进行重试。
2. **高级智能体推理思维 (Agentic Capabilities)** 针对复杂的物理操作（如跨平台跨目录的文件大批重构），Agent 不会死板地逐一调用极易卡死的 Shell 命令，而是具备“降维打击”的能力。它可以自发地先调用 `write` 编写一份利用 `shutil` 和 `os` 库的 Python 脚本，随后调用 `bash` 静默执行该脚本，完美绕开底层系统交互陷阱与 JSON 转义等隐患（*源于实际排障历史实践*）。
3. **严格的内存/Token 流控防御** 针对长程代码库分析中的常见崩溃点，系统设计了多道防线。单次抓取或搜索超过阈值时立刻进行字符串截断并附带提示（如 `[已截断，仅显示前 10000 字节]`）；在多轮对话中则动态评估（`estimate_tokens`），一旦即将超界便执行语义级别的历史备忘录降维压缩，保障运行成本与稳定性。
4. **高度的模块化解耦与协议抽象** 上层业务代码无需了解网络鉴权与 JSON 拼装细节，使得后端模型极其容易被平替（例如从 DeepSeek 无缝迁移至 Qwen、GLM）。同时，MCP 协议与 Skill 前置数据的引入，使得该框架随时能以外挂的方式接入各类新能力，而完全无需改动 `loop.py` 主循环内核。



## 四、案例

(openclaw) PS C:\Users\31283\Desktop\starter\mini-openclaw> python -m agent.cli "这是一项物理重构任务，请严格对绝对路径 'C:/Users/31283/Desktop/test/mini-openclaw' 进行操作（）。请你：1. 放弃使用 glob，直接使用 bash 工具执行 'dir C:/Users/31283/Desktop/test/mini-openclaw' 来扫描该目录下的混乱文件；2. 使用 write 工具在该目录下写一个自动分类和重命名的 Python 脚本 refactor.py；3. 使用 bash 工具执行 'python C:/Users/31283/Desktop/test/mini-openclaw/refactor.py' 脚本完成一键重构；4. 成功后，使用 write 工具在该目录下生成 README.md 说明结构。"

---

## ✅ 物理重构完成

### 目录结构（与 README 模块约定一致）

```
mini-openclaw/
├── agent/              ← 原 agent_core/，Agent 核心逻辑
│   ├── cli.py
│   ├── context.py
│   ├── loop.py
│   ├── prompts.py
│   └── __init__.py
├── backend/            ← 原 network/ 中的后端文件
│   ├── client.py
│   ├── server.py
│   ├── fake_backend.py
│   └── __init__.py
├── eval/               ← 原 evaluation/，评估模块
│   ├── ablation.py
│   ├── judge.py
│   ├── metrics.py
│   ├── tasks.py
│   ├── tracer.py
│   └── __init__.py
├── mcp/                ← MCP 协议相关（原 network/ 中分离）
│   ├── calc_server.py
│   ├── echo_server.py
│   ├── mcp_client.py
│   └── __init__.py
├── prompt/             ← 原 prompt_render/ 合并至此
│   ├── render.py
│   └── __init__.py
├── skills/             ← 技能模块（保持不变）
├── tools/              ← 工具模块（保持不变）
├── docs/               ← 文档/笔记
│   ├── Day2-开建Agent.ipynb
│   ├── report.md
│   └── t.txt
├── examples/           ← 示例代码
│   └── demo_m2.py
├── README.md
├── requirements.txt
├── .gitignore
├── refactor.py         ← 本次重构脚本
└── fix_structure.py    ← 之前遗留的脚本
```

### 主要变更

| 操作       | 说明                                  |
| ---------- | ------------------------------------- |
| **重命名** | `agent_core/` → `agent/`              |
| **重命名** | `evaluation/` → `eval/`               |
| **合并**   | `prompt_render/` → `prompt/`          |
| **拆分**   | `network/` → `backend/` + `mcp/`      |
| **整理**   | 杂项文件移入 `docs/` 和 `examples/`   |
| **清理**   | 删除所有空目录和 `.ipynb_checkpoints` |
| **补全**   | 为 `mcp/` 创建 `__init__.py`          |

现在项目结构清晰、命名一致，与 README 中定义的模块约定完全对齐。





(openclaw) PS C:\Users\31283\Desktop\starter\mini-openclaw> python -m agent.cli "这是一项纯粹的物理文件整理任务，请严格对绝对路径 C:/Users/31283/Desktop/学习资料/大二 进行操作。请你依次执行：1. 放弃使用 glob，直接使用 bash 工具执行命令 dir 'C:/Users/31283/Desktop/学习资料/大二' 来扫描该目录下的所有文件；2. 使用 write 工具在该目录下编写一个 Python 脚本 organize.py，脚本内容要求使用 os 和 shutil 模块，根据文件的扩展名或课程属性进行合理的分类建件夹并移动文件；3. 使用 bash 工具执行命令 python 'C:/Users/31283/Desktop/学习资料/大二/organize.py' 来完成一键物理分类；4. 执行成功后，使用 write 工具在该目录下生成一份 README.md，用中文说明整理后的目录结构。"
四项任务已全部完成！以下是执行总结：

---

## ✅ 任务完成报告

### 1️⃣ 目录扫描 ✅
使用 `dir` 命令扫描了 `C:/Users/31283/Desktop/学习资料/大二`，发现 **14 个课程/活动子目录** + 2 个根目录压缩包（`习概期末.zip`、`马原.zip`）。

### 2️⃣ 编写 organize.py ✅
已写入整理脚本，主要功能：
- **根目录松散文件归类**：将 `习概期末.zip` → `习概期末/`，`马原.zip` → `马原/`
- **习概期末嵌套目录上提**：将 `习概期末/习概期末/` 中的 5 个文件提到外层
- **大物实验文件归类**：按关键词将 PDF 移入对应的子实验文件夹（双臂电桥、非平衡电桥）

### 3️⃣ 执行整理 ✅
脚本运行成功，输出日志显示所有移动操作均正常完成。

### 4️⃣ 生成 README.md ✅
已生成中文说明文档，包含完整的目录树结构、整理操作说明和重新运行指引。
(openclaw) PS C:\Users\31283\Desktop\starter\mini-openclaw>



## 5、todo

[列出通过 grep 发现的 TODO 注释]

[列出发现的潜在待完善点或 Bug 风险]