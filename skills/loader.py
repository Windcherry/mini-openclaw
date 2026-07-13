"""Skills 加载器（Day9）。

Skill 与 Tool 的区别：
  - Tool 是一次函数调用（read 一个文件）。
  - Skill 是一包"领域知识 + 操作流程 + 可选脚本/资源"，用一个 SKILL.md 描述，
    在合适的时候被加载进上下文，告诉模型"面对这类任务该怎么一步步做"。

SKILL.md 结构（约定）：
  ---
  name: pdf-report
  description: 一句话说明何时该用这个 skill（用于召回判断）
  ---
  正文：步骤、注意事项、可调用的脚本路径、示例。

加载器要做：扫描 skills/ 下每个含 SKILL.md 的目录，解析 frontmatter，
按需把正文注入系统提示词 / 作为可发现的能力清单。
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


def parse_skill_md(text: str, path: Path) -> Skill:
    """解析 SKILL.md 的 YAML frontmatter（name/description）+ 正文 body。

    格式约定：
      ---
      name: skill-name
      description: 一句话说明
      ---
      正文（步骤、注意事项、示例等）
    """
    name = description = ""
    body = text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)   # 头尾两个 --- 之间是 frontmatter
        for line in fm.strip().split("\n"):
            line = line.strip()
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("description:"):
                description = line.split(":", 1)[1].strip()
    return Skill(name=name, description=description, body=body.strip(), path=path)


def load_skills(root: str = "skills") -> list[Skill]:
    """扫描 root 下所有 SKILL.md。"""
    skills: list[Skill] = []
    for md in Path(root).glob("*/SKILL.md"):
        skills.append(parse_skill_md(md.read_text(encoding="utf-8"), md))
    return skills


def skills_catalog(skills: list[Skill]) -> str:
    """生成给模型看的可用 skill 清单（name + description），用于按需召回。"""
    return "\n".join(f"- {s.name}: {s.description}" for s in skills)


def match_skills(task: str, skills: list[Skill]) -> list[Skill]:
    """按需召回：任务命中某 skill 的 description 关键词时，返回匹配的 skill 列表。

    匹配策略（简单但实用）：
      1. skill.name 出现在任务中 → 直接命中
      2. description 分词后的关键词出现在任务中 → 命中
    避免 skill 多时全部 body 占满上下文（呼应 Day4 上下文管理）。
    """
    task_lower = task.lower()
    matched: list[Skill] = []
    # 中文常见停用词，避免 bigram 误匹配
    STOP = {"一个", "这个", "那个", "什么", "用户", "使用", "需要", "可以",
            "进行", "通过", "以及", "对于", "关于", "或者", "但是", "不过",
            "这些", "那些", "此时", "时候", "一份", "一次"}
    for s in skills:
        # 策略 1：skill 名称命中
        if s.name.lower() in task_lower:
            matched.append(s)
            continue
        # 策略 2：description 分词后关键词命中
        desc = s.description
        for sep in ["/", "、", "，", "。", "：", "（", "）", " ", ",", "."]:
            desc = desc.replace(sep, "|")
        keywords = [w.strip().lower() for w in desc.split("|") if len(w.strip()) >= 2]
        # 策略 3：从长片段中再提取 2~4 字滑动窗口，补中文分词不足
        for chunk in list(keywords):
            if len(chunk) >= 4:
                for i in range(len(chunk) - 1):
                    bigram = chunk[i:i+2]
                    bigram = bigram.strip().lower()
                    if len(bigram) >= 2 and bigram not in STOP:
                        keywords.append(bigram)
        for kw in keywords:
            if kw in task_lower:
                matched.append(s)
                break
    return matched
