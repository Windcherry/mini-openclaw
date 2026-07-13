#!/usr/bin/env python3
"""确定性项目检测脚本 —— 扫描目录，输出结构化 JSON。

用途：省去模型反复写 glob/grep 组合来识别项目类型、入口点和配置文件。
skill 触发后直接执行此脚本获取项目元信息，再决定后续步骤。

用法：
  python scripts/detect_project.py [root_path] [--max-depth 3]
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path

# 语言/框架指纹：文件名 → (语言, 框架)
FINGERPRINTS = {
    "requirements.txt": ("Python", "pip"),
    "setup.py": ("Python", "setuptools"),
    "setup.cfg": ("Python", "setuptools"),
    "pyproject.toml": ("Python", "modern"),
    "Pipfile": ("Python", "pipenv"),
    "poetry.lock": ("Python", "poetry"),
    "package.json": ("JavaScript/TypeScript", "npm"),
    "yarn.lock": ("JavaScript/TypeScript", "yarn"),
    "pnpm-lock.yaml": ("JavaScript/TypeScript", "pnpm"),
    "tsconfig.json": ("TypeScript", None),
    "next.config.js": ("JavaScript/TypeScript", "Next.js"),
    "Cargo.toml": ("Rust", "Cargo"),
    "Cargo.lock": ("Rust", "Cargo"),
    "go.mod": ("Go", "Go Modules"),
    "Makefile": ("C/C++", "Make"),
    "CMakeLists.txt": ("C/C++", "CMake"),
    "pom.xml": ("Java", "Maven"),
    "build.gradle": ("Java/Kotlin", "Gradle"),
    "Gemfile": ("Ruby", "Bundler"),
    "composer.json": ("PHP", "Composer"),
    "Dockerfile": (None, "Docker"),
    "docker-compose.yml": (None, "Docker Compose"),
    ".github/workflows": (None, "GitHub Actions"),
}

# 入口点文件名模式
ENTRY_PATTERNS = [
    "main.py", "app.py", "run.py", "manage.py", "wsgi.py", "cli.py",
    "main.go", "cmd/*/main.go",
    "main.rs", "src/main.rs",
    "index.js", "index.ts", "server.js", "server.ts", "cli.js",
    "Main.java", "Application.java",
    "main.cpp", "main.c",
]

# 测试目录名
TEST_DIRS = {"tests", "test", "spec", "__tests__", "testcases"}


def scan(root: str, max_depth: int = 3) -> dict:
    """扫描目录树，返回结构化元信息。"""
    root = Path(root).resolve()
    file_types = Counter()
    config_files = []
    entry_points = []
    test_dirs = []
    all_files = []
    dirs = []

    for current_str, subdirs, files in os.walk(str(root)):
        current = Path(current_str)
        rel = current.relative_to(root)
        depth = len(rel.parts)

        if depth > max_depth:
            subdirs.clear()
            continue

        # 跳过隐藏目录和缓存
        subdirs[:] = [d for d in subdirs
                      if not d.startswith(".") and d not in ("__pycache__", "node_modules", "target", "venv", ".venv", "dist", "build")]

        dir_name = rel.parts[-1] if rel.parts else "."
        if depth <= 2:
            dirs.append(str(rel))

        if dir_name in TEST_DIRS and depth <= 2:
            test_dirs.append(str(rel))

        for f in files:
            filepath = current / f
            ext = filepath.suffix.lower()
            file_types[ext] += 1
            rel_path = str(rel / f)
            all_files.append(rel_path)

            # 识别配置文件
            if f in FINGERPRINTS:
                lang, framework = FINGERPRINTS[f]
                config_files.append({
                    "path": rel_path,
                    "file": f,
                    "language": lang,
                    "framework": framework,
                })

            # 识别入口点
            for pat in ENTRY_PATTERNS:
                if f == pat.split("/")[-1] or (pat.startswith("*") and f.startswith(pat[1:])):
                    entry_points.append(rel_path)
                    break

    # 推断主语言
    ext_to_lang = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".rs": "Rust", ".go": "Go", ".java": "Java", ".cpp": "C++",
        ".c": "C", ".rb": "Ruby", ".php": "PHP",
    }
    lang_scores = Counter()
    for ext, count in file_types.items():
        lang = ext_to_lang.get(ext)
        if lang:
            lang_scores[lang] += count
    primary_lang = lang_scores.most_common(1)[0][0] if lang_scores else "Unknown"

    return {
        "root": str(root),
        "primary_language": primary_lang,
        "file_count": len(all_files),
        "file_types": dict(file_types.most_common(20)),
        "config_files": config_files,
        "entry_points": sorted(set(entry_points)),
        "test_dirs": sorted(set(test_dirs)),
        "top_dirs": sorted(set(d.split("/")[0] for d in dirs if "/" not in d)),
        "subdirs": sorted(set(d for d in dirs if "/" in d))[:30],
    }


def main():
    import argparse
    p = argparse.ArgumentParser(description="Detect project structure")
    p.add_argument("root", nargs="?", default=".", help="Root directory to scan")
    p.add_argument("--max-depth", type=int, default=3, help="Max recursion depth")
    args = p.parse_args()

    result = scan(args.root, args.max_depth)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
