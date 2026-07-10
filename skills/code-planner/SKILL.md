---
name: code-planner
description: 当用户需要分析/审查/优化/调试一个代码仓库时使用。支持仓库结构可视化（Mermaid 图）、静态代码分析、冗余/低性能代码检测、Bug 追溯（git bisect）、修复建议生成。
---

# Code Planner Skill

## 何时使用

用户请求涉及以下任一场景时，加载此 Skill：
- 分析或理解一个代码仓库的结构
- 生成架构图、依赖图、类图等可视化图表
- 审查代码质量、查找冗余代码、性能热点
- 定位 Bug 引入的 commit，追溯改动历史
- 对代码提出优化建议并生成修复 diff
- 克隆 GitHub 仓库并进行全面分析

## 可用工具

本 Skill 使用以下专用工具（14 个）：

### 结构分析
| 工具 | 用途 |
|------|------|
| `git_clone` | 从 GitHub URL 克隆仓库到本地 |
| `repo_structure` | 扫描目录树，统计文件类型，识别入口点 |
| `mermaid_diagram` | 生成 Mermaid.js 架构图/依赖图/类图/模块关系图 |
| `dep_graph` | 分析 import 依赖，检测循环依赖 |
| `code_search` | 在仓库中搜索函数、类、import、调用点 |

### 代码质量
| 工具 | 用途 |
|------|------|
| `static_scan` | 运行 pylint（代码质量）/ radon（圈复杂度+可维护性指数） |
| `code_analyze` | 深度分析：结合静态扫描 + 源码，交 LLM 分析冗余代码、复杂度、模式问题 |
| `generate_diff` | 根据修复描述生成 unified diff 补丁 |
| `test_runner` | 发现并运行目标相关的测试 |

### Bug 追溯
| 工具 | 用途 |
|------|------|
| `git_bisect_start` | 启动 git bisect 二分查找 |
| `git_bisect_step` | 标记当前 commit 为 good/bad/skip，推进查找 |
| `git_bisect_reset` | 结束 bisect 会话，恢复原始状态 |
| `git_blame` | 逐行追溯代码的修改历史和作者 |
| `git_show_commit` | 展示指定 commit 的完整 diff 和元数据 |

## 工作流

### 流程 A：仓库结构分析

1. **获取仓库**: 如果是远程仓库 → `git_clone`；如果是本地 → 直接使用路径
2. **结构扫描**: `repo_structure(root_path=".", max_depth=3)` — 了解文件分布
3. **模块关系**: `dep_graph(root_path=".")` — 检查依赖关系和循环依赖
4. **生成图表**: `mermaid_diagram(root_path=".", diagram_type="architecture")` — 架构图
   - 还可生成 `dependency`（依赖图）、`class`（类图）、`module_relationship`（模块关系）
5. **输出**: 将 Mermaid 图写入 `diagrams/` 目录下的 `.md` 文件，可被 GitHub 渲染

### 流程 B：代码质量审查

1. **选择目标**: 可以是整个项目、一个目录或单个文件
2. **静态扫描**: `static_scan(target="path", tool="all")` — pylint + radon 全面扫描
3. **深度分析**: `code_analyze(file_path="path", focus="all")` — LLM 深入分析
   - 可指定 focus: `complexity`（复杂度）/ `redundancy`（冗余）/ `patterns`（设计模式）
4. **生成修复**: `generate_diff(file_path="path", description="...")` — 产出 unified diff
5. **验证**: `test_runner(root_path=".", target="path")` — 确保改动不破坏测试
6. **输出**: 将分析报告写入 `analysis_report.md`

### 流程 C：Bug 追溯

1. **确认范围**: 确定当前知道的情况 —— 哪个 commit 有 bug (bad)? 哪个 commit 正常 (good)?
2. **启动 bisect**: `git_bisect_start(repo_path=".", bad_commit="HEAD", good_commit="<sha>")`
3. **循环验证**: 对每个 bisect 给出的 commit：
   - 运行测试或手动检查 bug 是否存在
   - `git_bisect_step(repo_path=".", verdict="good"|"bad"|"skip")`
4. **找到根源**: bisect 完成后，会得到引入 bug 的第一个 commit
5. **分析改动**: `git_show_commit(repo_path=".", commit_hash="<sha>")` — 查看完整 diff
6. **追溯历史**: `git_blame(repo_path=".", file_path="path")` — 了解相关代码的改动历史
7. **修复**: 用 `code_analyze` + `generate_diff` 提出修复方案
8. **清理**: `git_bisect_reset(repo_path=".")` — 结束 bisect

## 注意事项

- **一次一步**: 每个工具的结果需要阅读后再决定下一步，不要臆测文件内容或工具输出
- **依赖安装**: `static_scan` 需要 pylint 和 radon (`pip install pylint radon`)
- **Git 环境**: `git_clone` / bisect 系列需要系统安装 git
- **图渲染**: Mermaid 图写入 `.md` 文件后，在 GitHub 或支持 Mermaid 的编辑器中可直接查看
- **安全**: `git_clone` 只克隆公开仓库；不要克隆包含敏感信息的私有仓库
- **LLM 分析**: `code_analyze` 和 `generate_diff` 返回上下文给模型推理——不是在工具内部调用 LLM，而是由 Agent 主循环将结果喂给后端模型
- **测试**: 修改代码前运行 `test_runner` 确保基线通过；修改后再次运行验证

## 输出约定

- 分析报告：写入 `analysis_report.md`
- Mermaid 图：写入 `diagrams/` 目录（如 `diagrams/architecture.md`、`diagrams/dependency.md`）
- Diff 补丁：写入 `fixes/` 目录（如 `fixes/optimize_xxx.patch`）
- 使用 `write` 工具写入文件，用 `read` 工具确认内容
