---
name: repo-onboarding
description: 当用户需要快速上手/理解/整理/重构/重命名一个代码仓库或目录结构时使用。覆盖项目结构盘点、依赖检索、推断文件功能、核心模块定位与运行验证、排查报错、文件移动与物理重排布、README 生成与导读报告输出。
---

# Repo Onboarding

帮助用户快速理解、整理和记录代码仓库。覆盖从结构扫描到物理重构再到文档输出的完整流程。

## 何时使用

- 第一次接触某个代码仓库，需要理解整体结构
- 清理混乱的目录结构，进行物理重排布与重命名
- 生成项目导读文档或 README
- 追踪某个功能涉及的文件调用链路
- 对比两个版本或分支的结构差异
- 遇到生僻的第三方依赖，需要查文档确认其作用

## 内置资源

| 资源 | 路径 | 用途 |
|------|------|------|
| 项目检测脚本 | `scripts/detect_project.py` | 确定性扫描项目结构，输出 JSON。执行 `python <skill_dir>/scripts/detect_project.py <root>` |
| 目录约定参考 | `references/directory-conventions.md` | Python/JS/Go/Rust 标准目录布局，重构时按需 read |
| 命名约定参考 | `references/naming-conventions.md` | 各语言命名规范与反模式，重命名时按需 read |
| 导读模板 | `assets/GUIDE_TEMPLATE.md` | GUIDE.md 输出模板，生成报告时 read 后填充 |

## 工作流

### 流程 A：标准仓库导读

1. **运行项目检测**
   ```
   bash("python skills/repo-onboarding/scripts/detect_project.py . --max-depth 3")
   ```
   从输出的 JSON 获取：主语言、文件数/类型、配置文件列表、入口点候选、测试目录。

2. **验证入口点**
   对检测到的入口点候选，用 `read` 确认内容。对不确定的文件：
   ```
   grep("^(def |class |func |fn |export )", path="<entry_file>")
   ```
   确认实际的启动逻辑。

3. **分析依赖**
   - `read` 依赖文件（requirements.txt / package.json / Cargo.toml / go.mod）
   - 遇到生僻库 → `web_fetch` 抓取 PyPI/npm/docs 页面确认用途
   - `dep_graph(root_path=".")` 分析内部模块依赖关系

4. **动态验证**
   ```
   bash("python <entry_point> --help 2>&1")
   bash("python -c 'import <core_module>' 2>&1")
   ```
   捕获 stdout/stderr。报错不是失败——它是确认代码状态的信号，阅读并分析根因。

5. **可视化架构**
   ```
   mermaid_diagram(root_path=".", diagram_type="architecture")
   mermaid_diagram(root_path=".", diagram_type="dependency")
   ```

6. **输出导读文档**
   `read` `assets/GUIDE_TEMPLATE.md`，按模板格式将分析结果写入 `GUIDE.md`。

### 流程 B：物理重排布与重命名

> 当目录混乱、文件命名不规范时使用。核心原则：**先规划后执行，批量操作用脚本。**

1. **审计现状**
   运行 `detect_project.py` 获取全貌。额外检查：
   ```
   grep("TODO", path=".")                          # 遗留 TODO 标记
   glob("**/test*.py")                              # 找放错位置的测试文件
   ```
   标记异常：命名模糊、位置错误、空目录、缓存残留。

2. **设计目标结构**
   `read` `references/directory-conventions.md` 获取对应语言的标准布局。
   参考 `references/naming-conventions.md` 制定命名规范。
   向用户展示规划，确认后再执行。

3. **创建目录**
   ```
   bash("mkdir -p src/ tests/ docs/ examples/")
   ```

4. **移动与重命名**
   - 简单 1:1 移动：`bash("mv old_path new_path")`
   - **批量/跨目录操作**：`write` 编写 Python 脚本（利用 `os`/`shutil`），然后 `bash("python refactor.py")` 一键执行
   - 每次移动后验证目标文件存在
   - 禁止 `rm -rf`，移动前先 `bash("ls <source>")` 确认

5. **更新引用**
   ```
   grep("<old_import>", path=".")     # 找受影响的 import
   edit(path="<file>", old="...", new="...")  # 逐文件修正
   bash("python -m pytest tests/ 2>&1 | tail -20")  # 验证测试
   ```

6. **清理收尾**
   ```
   bash("find . -type d -empty -delete")
   bash("find . -name '__pycache__' -exec rm -rf {} +")
   ```

### 流程 C：功能追踪

1. `grep("def <function_name>")` 定位定义
2. `grep("<function_name>(", path=".")` 搜索所有调用点
3. 逐层 `read` 追踪调用链，记录：输入 → 处理 → 输出
4. `write("TRACE_<feature>.md", ...)` 输出文字版调用图

### 流程 D：版本对比导读

1. `git_show_commit(commit_hash="HEAD", max_diff_lines=200)` 看近期热点
2. 在两个版本分别运行 `detect_project.py`，对比 JSON 差异

### 流程 E：生成 README

重构完成后执行。先 `bash("ls -la")` 确认实际目录结构（不凭记忆），再 `write("README.md", ...)`。

## 注意事项

- **先粗后细**：先全局扫描（`detect_project.py` / `repo_structure`），再深入具体文件
- **glob 搜路径，grep 搜内容**：两者互补，不用 `bash ls`/`find` 替代
- **降维打击**：复杂批量文件操作先 `write` Python 脚本再 `bash` 执行——比逐个调 bash 更稳
- **报错是信号**：bash 返回非零或 stderr 输出是动态验证的结果，分析并调整，而非放弃
- **边读边记**：关键发现立即写入输出文档
- **不臆测**：文件内容 `read` 确认，架构关系 `dep_graph` 验证，外部库 `web_fetch` 查证
- **适配语言**：根据 `detect_project.py` 输出的 `primary_language` 调整 glob/grep 模式

## 输出约定

| 输出 | 路径 | 触发条件 |
|------|------|---------|
| 导读报告 | `GUIDE.md` | 流程 A |
| 功能追踪 | `TRACE_<feature>.md` | 流程 C |
| 架构图 | `diagrams/*.md` | 流程 A 步骤5 |
| 重构脚本 | `refactor.py`（验证后删除） | 流程 B |
| README | `README.md` | 流程 E |
