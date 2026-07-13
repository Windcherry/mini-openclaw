"""LLM-as-judge：按领域专用 rubric 给代码库导读 Agent 的答复打分（1-5）。

Day4：用 DeepSeek API 做裁判，为 eval/tasks.py 中每个任务的 final 答复打分。
复用 D2 的 backend/client.py chat()——不用 tools，纯文本裁判。

与 tasks.py 的关系：
  - tasks.py 的 Task.check 是「程序化判据」（调了工具没？答复含关键词没？）——快但粗。
  - judge.py 是「语义判据」（答复质量高不高？）——慢但细。
  - 两者互补：check 筛掉明显不合格的，judge 给通过的答案打质量分。

设计要点（对应讲义 §9.3 偏差缓解）：
  1. rubric 固定并公开：每个领域目标有专属 RUBRIC，任何人重跑用同一把尺子。
  2. 先理由后打分：强制模型先给【理由】再给【分数】，比直接吐一个数字更稳。
  3. 领域锚定：rubric 里嵌入了项目大纲定义的架构层级和模块名，不做泛泛评分。

已知偏差（做完要说清）：
  - 长度偏差 (verbosity bias)：长回答容易被判"更全面"。
  - 位置偏差 (position bias)：靠后的回答容易得更高分（swap_test 可探测）。
  - 自我偏好 (self-preference)：LLM 倾向给自己风格的回答高分。
  - 分数漂移 (score drift)：同一回答在不同 batch 里可能差 ±1 分。
"""
from __future__ import annotations
import re
from typing import Any

from backend.client import DeepSeekBackend


# ============================================================================
# 领域专用 Rubric——按项目大纲五大目标分别定义
# ============================================================================

# 目标1：盘点现状与摸清骨架（scan-files / inspect-file / read-config / list-dir）
RUBRIC_SCAN = (
    "你是代码库导读 Agent 的严格评审。请按 1-5 分给【回答】打分。\n\n"
    "【背景】用户让 Agent 扫描项目文件或推断文件用途。\n"
    "【评分标准】\n"
    "  5=准确列出文件清单/目录分布，或准确推断出文件的业务功能（说出了它在项目中的角色）；\n"
    "  3=只列出了部分文件，或对文件用途的推断模糊（如只说'这是个工具文件'但没说清是什么工具）；\n"
    "  1=未列出文件，或对文件用途的推断完全错误。\n"
    "【注意】忽略措辞华丽程度。如果回答列出了具体文件名（.py/.md）或目录名，就是好回答。\n"
    "务必先写一行【理由】，再单独一行写【分数: X】（X 为 1-5 整数）。"
)

# 目标2：依赖与外部知识检索（check-deps / research-lib）
RUBRIC_DEPS = (
    "你是代码库导读 Agent 的严格评审。请按 1-5 分给【回答】打分。\n\n"
    "【背景】用户让 Agent 读取依赖配置或查询外部库的用途。\n"
    "【评分标准】\n"
    "  5=正确列出了所有关键依赖（httpx/pydantic/markdownify/pylint/radon），"
        "或准确解释了某个库的核心用途；\n"
    "  3=只列出了部分依赖，或对库的解释'方向对但不精确'（如只说 pydantic '做校验'但没说类型注解）；\n"
    "  1=依赖列表严重缺失或错误，或对库的解释完全跑题。\n"
    "【注意】判断'是否正确'时，以 requirements.txt 实际内容和官方文档为准。\n"
    "务必先写一行【理由】，再单独一行写【分数: X】（X 为 1-5 整数）。"
)

# 目标3：定位核心模块与动态验证（locate-entry / run-selfcheck / find-todos）
RUBRIC_LOCATE = (
    "你是代码库导读 Agent 的严格评审。请按 1-5 分给【回答】打分。\n\n"
    "【背景】用户让 Agent 搜索入口点、运行自检命令、或统计 TODO 注释。\n"
    "【评分标准】\n"
    "  5=准确指出了入口文件路径和关键函数名（如 agent/cli.py 的 main()），"
        "或正确报告了自检通过/失败状态，或给出了准确的 TODO 数量和代表性条目；\n"
    "  3=方向对但缺少具体路径（如只说'在 agent 目录下'但没说具体文件），"
        "或 TODO 数量大致对但具体条目不准确；\n"
    "  1=入口点找错、自检结果判断错误、或 TODO 统计完全离谱。\n"
    "【注意】项目中实际的入口点是 agent/cli.py 的 main()，TODO 约 33 个——"
        "不要求完全精确，但不应该差一个数量级。\n"
    "务必先写一行【理由】，再单独一行写【分数: X】（X 为 1-5 整数）。"
)

# 目标4：重排布与重命名（refactor-plan / refactor-execute）
RUBRIC_REFACTOR = (
    "你是代码库导读 Agent 的严格评审。请按 1-5 分给【回答】打分。\n\n"
    "【背景】用户让 Agent 设计重构方案或执行文件移动/重命名。\n"
    "【评分标准】\n"
    "  5=重构方案清晰合理（含目标目录结构），或成功执行了 mkdir/mv 并报告了结果；\n"
    "  3=方案存在但合理性存疑（如把所有文件塞进一个目录），或执行了操作但未确认结果；\n"
    "  1=方案完全不合理（如建议删除核心文件），或操作失败/未执行。\n"
    "【注意】标准项目结构是 agent/ backend/ tools/ mcp/ skills/ eval/ docs/ examples/。\n"
    "务必先写一行【理由】，再单独一行写【分数: X】（X 为 1-5 整数）。"
)

# 目标5：生成 README 与输出报告（generate-readme / output-report）——核心评测
RUBRIC_REPORT = (
    "你是代码库导读 Agent 的严格评审。请按 1-5 分给【模块导读报告】打分。\n\n"
    "【背景】用户让 Agent 输出 mini-OpenClaw 项目的完整模块导读报告。\n"
    "一份优秀的导读报告应覆盖以下四大层级（来自项目大纲）：\n"
    "  1) backend/ — LLM 交互层：client.py（DeepSeek API）、fake_backend.py（离线占位）；\n"
    "  2) agent/ — 主循环与上下文流控层：loop.py（ReAct 循环）、context.py（Token 管理）、prompts.py（系统提示词）；\n"
    "  3) tools/ + mcp/ + skills/ — 工具执行层：base.py（Tool 注册表）、fs.py（读写）、shell.py（bash）、"
        "code_analysis.py（8个分析工具）、git_ops.py（6个Git工具）、mcp/client.py（MCP客户端）、skills/loader.py（技能加载）；\n"
    "  4) eval/ — 评估基座：tasks.py（任务定义）、metrics.py（四维指标）、judge.py（LLM裁判）。\n\n"
    "【评分标准】\n"
    "  5=覆盖全部四层架构，每层列出了 ≥2 个关键模块并说明了用途，结构清晰、层级分明；\n"
    "  3=只覆盖了 2-3 层，或模块名有遗漏/错误，或缺少对模块用途的具体说明；\n"
    "  1=未按架构分层、几乎没列出模块名、纯泛泛而谈（如只说'项目结构还行'）。\n"
    "【注意】只依据【问题】判断【回答】，忽略长度与措辞。如果报告按四层组织、每层有具体文件名，就是好报告。\n"
    "务必先写一行【理由】，再单独一行写【分数: X】（X 为 1-5 整数）。"
)

# 基础能力（read-config / list-dir）——复用通用 rubric
RUBRIC_BASIC = (
    "你是代码库导读 Agent 的严格评审。请按 1-5 分给【回答】打分。\n\n"
    "【评分标准】\n"
    "  5=完全正确且直接命中问题（如准确报出了 timeout 值，或列出了正确的目录内容）；\n"
    "  3=部分正确或答非所问（如读了文件但报错值，或列了目录但不完整）；\n"
    "  1=错误或跑题（如没读文件就瞎猜，或根本不是在回答这个问题）。\n"
    "务必先写一行【理由】，再单独一行写【分数: X】（X 为 1-5 整数）。"
)

# 任务名 → rubric 的路由表（按前缀匹配）
RUBRIC_MAP: dict[str, str] = {
    # 基础能力
    "read-config":    RUBRIC_BASIC,
    "list-dir":       RUBRIC_BASIC,
    # 目标1：盘点骨架
    "scan-files":     RUBRIC_SCAN,
    "inspect-file":   RUBRIC_SCAN,
    # 目标2：依赖检索
    "check-deps":     RUBRIC_DEPS,
    "research-lib":   RUBRIC_DEPS,
    # 目标3：核心定位
    "locate-entry":   RUBRIC_LOCATE,
    "run-selfcheck":  RUBRIC_LOCATE,
    "find-todos":     RUBRIC_LOCATE,
    # 目标4：重排布
    "refactor-plan":    RUBRIC_REFACTOR,
    "refactor-execute": RUBRIC_REFACTOR,
    # 目标5：输出报告（最重量级）
    "generate-readme":  RUBRIC_REPORT,
    "output-report":    RUBRIC_REPORT,
}


# ============================================================================
# 裁判函数
# ============================================================================

def _rubric_for(task_name: str) -> str:
    """根据任务名选择对应的领域 rubric。未匹配时回退到通用 rubric。"""
    return RUBRIC_MAP.get(task_name, RUBRIC_BASIC)


def judge(question: str, answer: str, task_name: str = "") -> dict[str, Any]:
    """调用后端给一条回答打分。

    Args:
        question: 用户给 Agent 的指令
        answer: Agent 的最终答复 (traj["final"])
        task_name: 任务名，用于选择领域专用 rubric

    Returns:
        {"score": int|None, "raw": str, "task": str}
        score 为 None 表示正则未能提取分数——需人工抽查 raw。
    """
    rubric = _rubric_for(task_name)
    messages = [
        {"role": "system", "content": rubric},
        {"role": "user", "content": f"【问题】{question}\n【回答】{answer}"},
    ]
    resp = DeepSeekBackend().chat(messages)
    text = resp["content"]
    m = re.search(r"分数[:：]\s*([1-5])", text)
    score = int(m.group(1)) if m else None
    return {"score": score, "raw": text, "task": task_name}


def judge_trajectory(traj: dict, tasks_by_name: dict) -> dict[str, Any]:
    """给一条完整轨迹的 final 答复打分。

    Args:
        traj: 一条轨迹记录 {"task": ..., "steps": [...], "final": ...}
        tasks_by_name: {task_name: Task} 映射，用于获取 instruction

    Returns:
        {"task": str, "score": int|None, "final": str, "raw": str}
    """
    task_name = traj.get("task", "")
    task = tasks_by_name.get(task_name)
    instruction = task.instruction if task else ""
    result = judge(instruction, traj.get("final", ""), task_name)
    result["final"] = traj.get("final", "")
    return result


def batch_judge_trajectories(
    records: list[dict], tasks_by_name: dict
) -> list[dict[str, Any]]:
    """批量裁判所有轨迹记录。"""
    return [judge_trajectory(r, tasks_by_name) for r in records]


# ============================================================================
# 综合评定：程序化判据 × LLM 裁判
# ============================================================================

def evaluate_all(
    records: list[dict],
    tasks_by_name: dict,
) -> dict[str, Any]:
    """综合评定：每条轨迹先过程序化 check，再过 LLM judge。

    Returns:
        {
          "total": int,
          "check_pass": int,           # 程序化判据通过数
          "check_fail": int,           # 程序化判据失败数
          "judge_scores": [int|None],  # 每条轨迹的裁判分数
          "avg_score": float | None,   # 平均分（仅通过 check 的轨迹）
          "score_dist": {1: n, 2: n, ...},  # 分数分布
          "by_goal": {                  # 按五大目标分组
            "目标1-盘点骨架": {"count": int, "avg_score": float, "scores": [...]},
            ...
          },
          "details": [{task, check, score, raw_reason}, ...],
        }
    """
    results: list[dict] = []
    check_pass = 0
    check_fail = 0
    scores: list[int] = []
    score_dist: dict[int, int] = {i: 0 for i in range(1, 6)}

    # 按目标分组收集
    goal_groups: dict[str, list[int]] = {
        "基础能力": [],
        "目标1-盘点骨架": [],
        "目标2-依赖检索": [],
        "目标3-核心定位": [],
        "目标4-重排布": [],
        "目标5-输出报告": [],
    }

    def _goal_for(name: str) -> str:
        if name in ("read-config", "list-dir"):
            return "基础能力"
        if name in ("scan-files", "inspect-file"):
            return "目标1-盘点骨架"
        if name in ("check-deps", "research-lib"):
            return "目标2-依赖检索"
        if name in ("locate-entry", "run-selfcheck", "find-todos"):
            return "目标3-核心定位"
        if name in ("refactor-plan", "refactor-execute"):
            return "目标4-重排布"
        if name in ("generate-readme", "output-report"):
            return "目标5-输出报告"
        return "基础能力"

    for r in records:
        task_name = r.get("task", "")
        task = tasks_by_name.get(task_name)
        check_ok = task.check(r) if task else False

        if check_ok:
            check_pass += 1
        else:
            check_fail += 1

        # 对所有轨迹都跑 judge（包括 check 失败的——可以看模型怎么评）
        jr = judge_trajectory(r, tasks_by_name)
        score = jr.get("score")
        if score is not None:
            scores.append(score)
            score_dist[score] += 1
            goal_groups[_goal_for(task_name)].append(score)

        results.append({
            "task": task_name,
            "check": check_ok,
            "score": score,
            "raw_reason": jr["raw"].split("\n")[0] if jr.get("raw") else "",
            "final": r.get("final", "")[:120],
        })

    # 聚合
    avg = sum(scores) / len(scores) if scores else None
    goal_summary = {}
    for goal, gscores in goal_groups.items():
        goal_summary[goal] = {
            "count": len(gscores),
            "avg_score": sum(gscores) / len(gscores) if gscores else None,
            "scores": gscores,
        }

    return {
        "total": len(records),
        "check_pass": check_pass,
        "check_fail": check_fail,
        "check_pass_rate": check_pass / max(len(records), 1),
        "avg_score": avg,
        "score_dist": score_dist,
        "by_goal": goal_summary,
        "details": results,
    }


# ============================================================================
# 偏差检测工具
# ============================================================================

def swap_test(question: str, answer_a: str, answer_b: str,
              task_name: str = "") -> dict:
    """顺序偏差检测：A-B → B-A 各打一次，检查是否一致。

    如果 judge 无偏，answer_a 两次得分应一致。
    """
    r1 = judge(question, answer_a, task_name)
    r2 = judge(question, answer_b, task_name)
    # 换序再评
    r1_swapped = judge(question, answer_a, task_name)  # 第二次独立调用
    r2_swapped = judge(question, answer_b, task_name)

    return {
        "run1": {"a": r1["score"], "b": r2["score"]},
        "run2": {"a": r1_swapped["score"], "b": r2_swapped["score"]},
        "a_consistent": r1["score"] == r1_swapped["score"],
        "b_consistent": r2["score"] == r2_swapped["score"],
    }


def head_to_head(question: str, answer_a: str, answer_b: str) -> dict:
    """成对比较：直接让 judge 判定 A vs B 谁更好。"""
    prompt = (
        "你是代码库导读报告的严格评审。下面是对同一个问题的两个回答。请判断哪个更好。\n"
        f"【问题】{question}\n\n"
        f"【回答 A】{answer_a}\n\n"
        f"【回答 B】{answer_b}\n\n"
        "先写一行【理由】，再单独一行写【胜者: A/B/平局】。"
    )
    resp = DeepSeekBackend().chat([
        {"role": "system", "content": "你是代码库导读报告的严格评审。"},
        {"role": "user", "content": prompt},
    ])
    text = resp["content"]
    m = re.search(r"胜者[:：]\s*([AB]|平局)", text)
    winner = m.group(1) if m else None
    return {"winner": winner, "raw": text}

# ============================================================================
# 使用方式（供外部调用）：
#   from eval.judge import judge, evaluate_all, swap_test, head_to_head
#   result = judge(question, answer, task_name)
#   report = evaluate_all(records, tasks_by_name)
# ============================================================================
