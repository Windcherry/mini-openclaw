"""Code Planner 工具集：仓库结构分析、Mermaid 图生成、静态分析、深度代码分析。

Day6+ v1 起可用。每个工具遵循 text-in/text-out 约定：
  Tool.run(**arguments) -> str（observation 文本）
"""
from __future__ import annotations
import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import Tool


# ---------------------------------------------------------------------------
# 通用工具函数
# ---------------------------------------------------------------------------

def _is_python_file(path: str) -> bool:
    return path.endswith(".py")


def _should_skip_dir(dirname: str) -> bool:
    return dirname.startswith(".") or dirname in (
        "__pycache__", "node_modules", ".git", ".venv", "venv", "build", "dist",
        ".tox", ".eggs", "*.egg-info",
    )


def _should_skip_file(filename: str) -> bool:
    return filename.startswith(".") or filename.endswith((".pyc", ".pyo", ".so", ".pyd"))


# ---------------------------------------------------------------------------
# 1. repo_structure
# ---------------------------------------------------------------------------

def _repo_structure(root_path: str = ".", max_depth: int = 3) -> str:
    """扫描目录树，统计文件类型，识别入口点与主要模块。"""
    root = Path(root_path).resolve()
    if not root.exists():
        return f"错误：路径不存在 —— {root_path}"
    if not root.is_dir():
        return f"错误：不是目录 —— {root_path}"

    lines: list[str] = []
    ext_counts: dict[str, int] = {}
    py_modules: list[str] = []
    entry_points: list[str] = []
    total_files = 0

    def _walk(current: Path, depth: int, prefix: str = "") -> None:
        nonlocal total_files
        if depth > max_depth:
            return
        entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name))
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            if entry.is_dir():
                if _should_skip_dir(entry.name):
                    lines.append(f"{prefix}{connector}{entry.name}/ (跳过)")
                    continue
                lines.append(f"{prefix}{connector}{entry.name}/")
                ext = "/"  # directory marker
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
                _walk(entry, depth + 1, prefix + ("    " if is_last else "│   "))
            else:
                if _should_skip_file(entry.name):
                    continue
                total_files += 1
                lines.append(f"{prefix}{connector}{entry.name}")
                ext = entry.suffix or "(无扩展名)"
                ext_counts[ext] = ext_counts.get(ext, 0) + 1

                if _is_python_file(entry.name):
                    py_modules.append(str(entry.relative_to(root)))
                    # 检测入口点
                    _check_entry_point(entry, root)

    def _check_entry_point(filepath: Path, root: Path) -> None:
        try:
            source = filepath.read_text(encoding="utf-8")
        except Exception:
            return
        if filepath.name == "__main__.py":
            entry_points.append(f"  {filepath.relative_to(root)} (__main__.py 入口)")
        elif 'if __name__ == "__main__"' in source or "if __name__ == '__main__'" in source:
            entry_points.append(f"  {filepath.relative_to(root)} (含 __main__ 块)")

    lines.append(f"{root.name}/")
    _walk(root, depth=1)

    # 汇总
    lines.append(f"\n总文件数: {total_files}")
    lines.append(f"Python 模块数: {len(py_modules)}")
    lines.append(f"\n文件类型分布:")
    for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1]):
        label = "目录" if ext == "/" else (ext or "(无扩展名)")
        lines.append(f"  {label}: {count}")

    if entry_points:
        lines.append(f"\n入口点 ({len(entry_points)}):")
        lines.extend(entry_points)

    return "\n".join(lines)


repo_structure_tool = Tool(
    name="repo_structure",
    description="扫描仓库目录树，统计文件类型分布，识别入口点和主要 Python 模块。",
    parameters={
        "type": "object",
        "properties": {
            "root_path": {"type": "string", "description": "仓库本地路径，默认当前目录 '.'"},
            "max_depth": {"type": "integer", "description": "最大扫描深度，默认 3"},
        },
        "required": ["root_path"],
    },
    run=_repo_structure,
)


# ---------------------------------------------------------------------------
# 2. mermaid_diagram
# ---------------------------------------------------------------------------

def _mermaid_diagram(root_path: str = ".", diagram_type: str = "architecture") -> str:
    """解析 Python 源码结构，生成 Mermaid.js 图。"""
    root = Path(root_path).resolve()
    if not root.exists():
        return f"错误：路径不存在 —— {root_path}"

    modules: dict[str, list[dict]] = {}    # module_path -> [{name, type, methods}]
    imports: dict[str, list[str]] = {}     # module_path -> [imported_module, ...]
    classes: dict[str, dict] = {}          # class_name -> {module, bases, methods}

    # 收集所有模块信息
    for py_file in sorted(root.rglob("*.py")):
        if any(part.startswith(".") or part in ("__pycache__",) for part in py_file.parts):
            continue
        rel = str(py_file.relative_to(root))
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
        except (SyntaxError, UnicodeDecodeError):
            continue

        mod_defs: list[dict] = []
        mod_imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [n.name for n in ast.walk(node) if isinstance(n, ast.FunctionDef)]
                mod_defs.append({"name": node.name, "type": "class", "methods": methods})
                classes[f"{rel}::{node.name}"] = {
                    "module": rel, "bases": [_name_of(b) for b in node.bases], "methods": methods,
                }
            elif isinstance(node, ast.FunctionDef) and isinstance(getattr(node, "parent", None), ast.Module):
                # 顶层函数（parent 需手动标记，此处简化：检查是否在 class 内）
                pass
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        mod_imports.append(alias.name)
                elif node.module:
                    mod_imports.append(node.module)

        # 更简单的方式：重新遍历顶层节点
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef):
                mod_defs.append({"name": node.name, "type": "function", "methods": []})
            elif isinstance(node, ast.ClassDef):
                methods = [n.name for n in ast.iter_child_nodes(node) if isinstance(n, ast.FunctionDef)]
                mod_defs.append({"name": node.name, "type": "class", "methods": methods})

        if mod_defs or mod_imports:
            modules[rel] = {"defs": mod_defs, "imports": mod_imports}

    # 按图表类型生成
    if diagram_type == "architecture":
        return _gen_architecture(modules, root)
    elif diagram_type == "dependency":
        return _gen_dependency_graph(modules)
    elif diagram_type == "class":
        return _gen_class_diagram(modules, classes)
    elif diagram_type == "module_relationship":
        return _gen_module_graph(modules)
    else:
        return _gen_architecture(modules, root)


def _name_of(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}"
    return "?"


def _gen_architecture(modules: dict, root: Path) -> str:
    """架构图：按目录分层，展示模块及关键类/函数。"""
    lines = ["```mermaid", "graph TD"]
    # 按目录分组
    groups: dict[str, list[str]] = {}
    for mod_path in sorted(modules):
        parts = mod_path.split("/")
        group = parts[0] if len(parts) > 1 else "(根)"
        groups.setdefault(group, [])
        mod_name = mod_path.replace("/", ".").removesuffix(".py")
        node_id = mod_name.replace(".", "_").replace("-", "_")
        defs = modules[mod_path]["defs"]
        label = f"{mod_name}<br/>" + "<br/>".join(
            f"{'🔷' if d['type']=='class' else '🔹'} {d['name']}" for d in defs[:5]
        )
        if len(defs) > 5:
            label += f"<br/>... (+{len(defs)-5})"
        lines.append(f'    {node_id}["{label}"]')
        groups[group].append(mod_name)

    # 子图分组
    for group, mods in groups.items():
        if len(mods) > 1:
            ids = ", ".join(m.replace(".", "_").replace("-", "_") for m in mods)
            lines.append(f"    subgraph {group} [{group}/]")
            for m in mods:
                lines.append(f"        {m.replace('.', '_').replace('-', '_')}")
            lines.append("    end")

    lines.append("```")
    return "\n".join(lines)


def _gen_dependency_graph(modules: dict) -> str:
    """依赖图：模块间的 import 关系。"""
    lines = ["```mermaid", "graph LR"]
    added: set[str] = set()
    for mod_path, info in sorted(modules.items()):
        src_id = mod_path.replace("/", ".").removesuffix(".py").replace(".", "_").replace("-", "_")
        if src_id not in added:
            lines.append(f'    {src_id}["{mod_path}"]')
            added.add(src_id)
        for imp in info["imports"]:
            # 只显示仓库内部的 import
            imp_base = imp.split(".")[0]
            for other in modules:
                other_id = other.replace("/", ".").removesuffix(".py")
                if other_id.startswith(imp_base):
                    tgt_id = other_id.replace(".", "_").replace("-", "_")
                    if tgt_id not in added:
                        lines.append(f'    {tgt_id}["{other}"]')
                        added.add(tgt_id)
                    lines.append(f"    {src_id} --> {tgt_id}")
    lines.append("```")
    return "\n".join(lines)


def _gen_class_diagram(modules: dict, classes: dict) -> str:
    """类图：类及其方法、继承关系。"""
    lines = ["```mermaid", "classDiagram"]
    for class_full, info in sorted(classes.items()):
        # 格式: module::ClassName
        short = class_full.split("::")[-1]
        lines.append(f"    class {short} {{")
        for m in info["methods"][:10]:
            lines.append(f"        +{m}()")
        if len(info["methods"]) > 10:
            lines.append(f"        ... (+{len(info['methods'])-10})")
        lines.append("    }")
        for base in info["bases"]:
            if base not in ("object", "Exception", "BaseException", "ABC"):
                lines.append(f"    {base} <|-- {short}")
    lines.append("```")
    return "\n".join(lines)


def _gen_module_graph(modules: dict) -> str:
    """模块关系图：按 top-level package 展示模块间关系。"""
    lines = ["```mermaid", "graph TD"]
    pkgs: dict[str, list[str]] = {}
    for mod_path in modules:
        top = mod_path.split("/")[0]
        pkgs.setdefault(top, []).append(mod_path)

    for pkg_name, mods in sorted(pkgs.items()):
        if len(mods) > 1:
            lines.append(f"    subgraph {pkg_name} [{pkg_name}]")
            for m in mods:
                node_id = m.replace("/", ".").removesuffix(".py").replace(".", "_").replace("-", "_")
                lines.append(f"        {node_id}[{m}]")
            lines.append("    end")
        else:
            node_id = mods[0].replace("/", ".").removesuffix(".py").replace(".", "_").replace("-", "_")
            lines.append(f'    {node_id}["{mods[0]}"]')

    lines.append("```")
    return "\n".join(lines)


mermaid_diagram_tool = Tool(
    name="mermaid_diagram",
    description="生成 Mermaid.js 格式的架构图/依赖图/类图/模块关系图，可直接在 GitHub Markdown 渲染。",
    parameters={
        "type": "object",
        "properties": {
            "root_path": {"type": "string", "description": "仓库本地路径"},
            "diagram_type": {
                "type": "string",
                "enum": ["architecture", "dependency", "class", "module_relationship"],
                "description": "图表类型：architecture=架构图, dependency=依赖图, class=类图, module_relationship=模块关系图",
            },
        },
        "required": ["root_path"],
    },
    run=_mermaid_diagram,
)


# ---------------------------------------------------------------------------
# 3. static_scan — 对接 pylint / radon
# ---------------------------------------------------------------------------

def _static_scan(target: str, tool: str = "all") -> str:
    """对指定文件/目录运行 pylint 或 radon。"""
    target_path = Path(target).resolve()
    if not target_path.exists():
        return f"错误：目标不存在 —— {target}"

    results: list[str] = []
    results.append(f"== 静态分析: {target} (tool={tool}) ==\n")

    if tool in ("pylint", "all"):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pylint", str(target_path), "-f", "text"],
                capture_output=True, text=True, timeout=120, cwd=str(target_path.parent),
            )
            output = proc.stdout.strip() or proc.stderr.strip() or "(pylint 无输出)"
            results.append(f"--- pylint ---\n{output}")
        except FileNotFoundError:
            results.append("--- pylint ---\n[pylint 未安装] pip install pylint")
        except subprocess.TimeoutExpired:
            results.append("--- pylint ---\n[超时] 分析时间过长")

    if tool in ("radon_cc", "all"):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "radon", "cc", str(target_path), "-s"],
                capture_output=True, text=True, timeout=60, cwd=str(target_path.parent),
            )
            output = proc.stdout.strip() or "(radon cc 无输出)"
            results.append(f"--- radon 圈复杂度 ---\n{output}")
        except FileNotFoundError:
            results.append("--- radon 圈复杂度 ---\n[radon 未安装] pip install radon")

    if tool in ("radon_mi", "all"):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "radon", "mi", str(target_path), "-s"],
                capture_output=True, text=True, timeout=60, cwd=str(target_path.parent),
            )
            output = proc.stdout.strip() or "(radon mi 无输出)"
            results.append(f"--- radon 可维护性指数 ---\n{output}")
        except FileNotFoundError:
            results.append("--- radon 可维护性指数 ---\n[radon 未安装] pip install radon")

    return "\n\n".join(results)


static_scan_tool = Tool(
    name="static_scan",
    description="对指定文件或目录运行静态分析（pylint 代码质量 / radon 圈复杂度+可维护性指数），返回问题清单。",
    parameters={
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "文件或目录路径"},
            "tool": {
                "type": "string",
                "enum": ["pylint", "radon_cc", "radon_mi", "all"],
                "description": "分析工具：pylint=代码质量, radon_cc=圈复杂度, radon_mi=可维护性指数, all=全部",
            },
        },
        "required": ["target"],
    },
    run=_static_scan,
)


# ---------------------------------------------------------------------------
# 4. code_analyze — LLM 驱动的深度分析（上下文收集器）
# ---------------------------------------------------------------------------

def _code_analyze(file_path: str, focus: str = "all") -> str:
    """深度分析：收集源码 + 静态分析结果，格式化供模型推理。

    本工具不直接调用 LLM。它将所有上下文组织好作为 observation 返回，
    由 Agent 主循环将结果喂给模型，模型在下一轮生成分析结论。
    """
    fp = Path(file_path).resolve()
    if not fp.exists():
        return f"错误：文件不存在 —— {file_path}"
    if not fp.is_file():
        return f"错误：不是文件 —— {file_path}"

    try:
        source = fp.read_text(encoding="utf-8")
    except Exception as e:
        return f"错误：无法读取文件 —— {e}"

    lines = source.split("\n")
    total_lines = len(lines)

    # 运行静态分析
    static_result = _static_scan(str(fp), tool="all")

    # 基本统计
    num_funcs = len([1 for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef)])
    num_classes = len([1 for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ClassDef)])

    focus_desc = {
        "complexity": "重点分析圈复杂度高的函数、深层嵌套、过长函数，给出简化方案",
        "redundancy": "重点分析重复代码块、无用变量、冗余条件判断、可合并的逻辑",
        "patterns": "重点分析设计模式问题：是否可以用更合适的模式、是否有反模式（god class, shotgun surgery）",
        "all": "综合分析复杂度、冗余、模式问题，以及任何其他可改进之处",
    }.get(focus, "")

    return f"""== 深度代码分析上下文 ==
文件: {file_path}
行数: {total_lines}
函数数: {num_funcs}  类数: {num_classes}
分析重点: {focus_desc}

--- 静态分析结果 ---
{static_result}

--- 源码 ---
（行号前缀，共 {total_lines} 行）
""" + "\n".join(f"{i+1:4d}| {line}" for i, line in enumerate(lines)) + f"""

---
请基于以上上下文，输出一个结构化的分析报告：
1. **问题摘要**：按严重程度（🔴高 / 🟡中 / 🟢低）列出发现的问题
2. **每个问题的详情**：位置（行号）、原因、影响、修复建议
3. **优化后代码示例**（如适用）
4. **优先级建议**：哪些问题应先修复
"""


code_analyze_tool = Tool(
    name="code_analyze",
    description="深度代码分析：结合静态扫描结果与源码，交由 LLM 分析冗余代码、复杂度热点和可优化模式。",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "要分析的文件路径"},
            "focus": {
                "type": "string",
                "enum": ["complexity", "redundancy", "patterns", "all"],
                "description": "分析重点：complexity=复杂度, redundancy=冗余代码, patterns=设计模式, all=综合",
            },
        },
        "required": ["file_path"],
    },
    run=_code_analyze,
)


# ---------------------------------------------------------------------------
# 5. generate_diff — 生成 unified diff 补丁
# ---------------------------------------------------------------------------

def _generate_diff(file_path: str, description: str) -> str:
    """根据修复描述，读取文件并生成 diff 上下文，供模型产出 unified diff。"""
    fp = Path(file_path).resolve()
    if not fp.exists():
        return f"错误：文件不存在 —— {file_path}"

    try:
        source = fp.read_text(encoding="utf-8")
    except Exception as e:
        return f"错误：无法读取文件 —— {e}"

    lines = source.split("\n")

    return f"""== 生成 Diff 上下文 ==
目标文件: {file_path}
修复描述: {description}

--- 当前源码（共 {len(lines)} 行）---
""" + "\n".join(f"{i+1:4d}| {line}" for i, line in enumerate(lines)) + f"""

---
请基于以上修复描述和源码，生成 unified diff：
1. 使用标准的 unified diff 格式（--- / +++ / @@）
2. 只修改必要的行，保持最小改动
3. 在 diff 前后附简要说明：改了什么、为什么改
"""


generate_diff_tool = Tool(
    name="generate_diff",
    description="根据修复描述和当前源码，生成 unified diff 补丁。工具收集上下文，模型产出实际 diff。",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "要修改的文件路径"},
            "description": {"type": "string", "description": "自然语言修复描述，如 '将 O(n²) 的嵌套循环替换为 O(n) 的字典查找'"},
        },
        "required": ["file_path", "description"],
    },
    run=_generate_diff,
)


# ---------------------------------------------------------------------------
# 6. code_search — 跨文件代码搜索
# ---------------------------------------------------------------------------

def _code_search(root_path: str, query: str, search_type: str = "regex") -> str:
    """在仓库中搜索函数定义、类定义、import、调用点、或正则模式。"""
    root = Path(root_path).resolve()
    if not root.exists():
        return f"错误：路径不存在 —— {root_path}"

    results: list[str] = []
    results.append(f"== 代码搜索: query='{query}', type={search_type} ==\n")
    found = 0

    for py_file in sorted(root.rglob("*.py")):
        if any(part.startswith(".") for part in py_file.parts if part != "."):
            continue
        if "__pycache__" in py_file.parts:
            continue

        try:
            source = py_file.read_text(encoding="utf-8")
            if search_type == "regex":
                import re
                for i, line in enumerate(source.split("\n"), 1):
                    if re.search(query, line):
                        rel = py_file.relative_to(root)
                        results.append(f"  {rel}:{i}: {line.strip()[:120]}")
                        found += 1
                        if found > 50:
                            results.append(f"  ... (截断，共 >50 条匹配)")
                            return "\n".join(results)
            else:
                tree = ast.parse(source, filename=str(py_file))
                for node in ast.walk(tree):
                    hit = None
                    if search_type == "function_def" and isinstance(node, ast.FunctionDef):
                        if query.lower() in node.name.lower():
                            hit = f"def {node.name}()"
                    elif search_type == "class_def" and isinstance(node, ast.ClassDef):
                        if query.lower() in node.name.lower():
                            hit = f"class {node.name}"
                    elif search_type == "import" and isinstance(node, (ast.Import, ast.ImportFrom)):
                        names = [a.name for a in node.names]
                        if any(query.lower() in n.lower() for n in names):
                            hit = f"import {', '.join(names)}"
                    elif search_type == "call_site" and isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name) and query.lower() in node.func.id.lower():
                            hit = f"{node.func.id}()"
                    if hit:
                        rel = py_file.relative_to(root)
                        results.append(f"  {rel}:{node.lineno}: {hit}")
                        found += 1
                        if found > 50:
                            results.append(f"  ... (截断，共 >50 条匹配)")
                            return "\n".join(results)
        except (SyntaxError, UnicodeDecodeError):
            continue

    if found == 0:
        results.append("  (未找到匹配项)")
    else:
        results.append(f"\n共找到 {found} 条匹配")
    return "\n".join(results)


code_search_tool = Tool(
    name="code_search",
    description="在仓库中按类型搜索函数定义、类定义、import、调用点或正则模式。",
    parameters={
        "type": "object",
        "properties": {
            "root_path": {"type": "string", "description": "仓库根路径"},
            "query": {"type": "string", "description": "搜索关键词或正则表达式"},
            "search_type": {
                "type": "string",
                "enum": ["function_def", "class_def", "import", "call_site", "regex"],
                "description": "搜索类型：function_def=函数定义, class_def=类定义, import=导入, call_site=调用点, regex=正则",
            },
        },
        "required": ["root_path", "query"],
    },
    run=_code_search,
)


# ---------------------------------------------------------------------------
# 7. dep_graph — 模块依赖图 + 循环依赖检测
# ---------------------------------------------------------------------------

def _dep_graph(root_path: str = ".") -> str:
    """分析 Python 模块间的 import 依赖关系，检测循环依赖。"""
    root = Path(root_path).resolve()
    if not root.exists():
        return f"错误：路径不存在 —— {root_path}"

    # 构建内部模块列表
    internal: dict[str, set[str]] = {}  # module_path -> {imported modules}
    for py_file in sorted(root.rglob("*.py")):
        if any(part.startswith(".") for part in py_file.parts if part != "."):
            continue
        if "__pycache__" in py_file.parts:
            continue
        rel = str(py_file.relative_to(root).with_suffix("")).replace("/", ".").replace("\\", ".")
        internal[rel] = set()
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    internal[rel].add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                internal[rel].add(node.module.split(".")[0])

    # 转换为 top-level package 的依赖
    lines: list[str] = []
    lines.append("== 模块依赖分析 ==\n")

    # 依赖矩阵（只关注仓库内的 top-level package）
    pkgs = sorted(set(rel.split(".")[0] for rel in internal))
    deps: dict[str, set[str]] = {p: set() for p in pkgs}
    for mod, imports in internal.items():
        src_pkg = mod.split(".")[0]
        for imp in imports:
            if imp in deps:
                deps[src_pkg].add(imp)

    for pkg in pkgs:
        if deps[pkg]:
            lines.append(f"  {pkg}/ → {', '.join(sorted(deps[pkg]))}")

    # 循环依赖检测（DFS）
    lines.append("\n--- 循环依赖检测 ---")
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {p: WHITE for p in pkgs}
    cycles: list[list[str]] = []

    def dfs(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        stack.append(node)
        for neighbor in deps.get(node, set()):
            if color.get(neighbor, WHITE) == GRAY:
                cycle_start = stack.index(neighbor)
                cycles.append(stack[cycle_start:] + [neighbor])
            elif color.get(neighbor, WHITE) == WHITE:
                dfs(neighbor, stack)
        stack.pop()
        color[node] = BLACK

    for pkg in pkgs:
        if color[pkg] == WHITE:
            dfs(pkg, [])

    real_cycles = [c for c in cycles if len(set(c)) > 1]
    if real_cycles:
        for cycle in real_cycles:
            lines.append(f"  ⚠️ 循环依赖: {' → '.join(cycle)}")
    else:
        lines.append("  ✅ 未检测到循环依赖")

    return "\n".join(lines)


dep_graph_tool = Tool(
    name="dep_graph",
    description="分析模块间的 import 依赖关系，构建依赖图，检测循环依赖。",
    parameters={
        "type": "object",
        "properties": {
            "root_path": {"type": "string", "description": "仓库根路径"},
        },
        "required": ["root_path"],
    },
    run=_dep_graph,
)


# ---------------------------------------------------------------------------
# 8. test_runner — 测试发现与执行
# ---------------------------------------------------------------------------

def _test_runner(root_path: str, target: str, framework: str = "pytest") -> str:
    """发现并运行与目标文件相关的测试。"""
    root = Path(root_path).resolve()
    if not root.exists():
        return f"错误：路径不存在 —— {root_path}"

    target_path = Path(target).resolve() if not Path(target).is_absolute() else Path(target)
    if not target_path.exists():
        return f"错误：目标不存在 —— {target}"

    results: list[str] = []
    results.append(f"== 测试运行: {target} (framework={framework}) ==\n")

    # 目标如果是目录，直接在该目录运行
    if target_path.is_dir():
        test_target = str(target_path)
    else:
        # 尝试找对应的测试文件
        stem = target_path.stem
        test_candidates = list(root.rglob(f"test_{stem}.py")) + list(root.rglob(f"test_{stem}*")) + list(root.rglob(f"{stem}_test.py"))
        if test_candidates:
            test_target = str(test_candidates[0])
            results.append(f"发现测试文件: {test_candidates[0].relative_to(root)}")
        else:
            test_target = str(target_path)
            results.append("(未找到对应测试文件，在原目录运行)")

    try:
        if framework == "pytest":
            cmd = [sys.executable, "-m", "pytest", test_target, "-v", "--tb=short", f"--rootdir={root}"]
        else:
            cmd = [sys.executable, "-m", "unittest", "discover", "-s", str(root), "-p", f"test_{target_path.name}*"]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, cwd=str(root),
        )
        output = proc.stdout.strip() or proc.stderr.strip() or "(无输出)"
        results.append(output)
    except FileNotFoundError:
        results.append(f"[{framework} 未安装] pip install {framework}")
    except subprocess.TimeoutExpired:
        results.append("[超时] 测试运行时间过长")

    return "\n".join(results)


test_runner_tool = Tool(
    name="test_runner",
    description="发现并运行指定文件或模块相关的测试，返回测试结果。",
    parameters={
        "type": "object",
        "properties": {
            "root_path": {"type": "string", "description": "仓库根路径"},
            "target": {"type": "string", "description": "目标文件或模块路径"},
            "framework": {
                "type": "string",
                "enum": ["pytest", "unittest"],
                "description": "测试框架，默认 pytest",
            },
        },
        "required": ["root_path", "target"],
    },
    run=_test_runner,
)
