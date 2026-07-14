"""任务规划与状态机（Day11+，讲义 §4/§6/§7）。

治迷失的核心：把大目标拆成有序、可跟踪的子任务，并把清单常驻上下文。
Agent 每轮都能看到当前进度，知道下一步该做什么。

反思（讲义 §6）：在子任务完成后 / 命令失败后插一步自我审视，发现问题就修正，
并设上限（同一子任务最多反思 N 次），避免无限套娃。

错误恢复（讲义 §7）：分类失败 ——
  - TransientError → 重试 + 指数退避
  - PermanentError → 重规划（改 todo）
  - 反复失败 → block，先做别的（单步失败 ≠ 整体失败）
"""
from __future__ import annotations
import time
from typing import Callable, TypeVar

T = TypeVar("T")

# ══════════════════════════════════════════════════════════════════════
# 错误分类（讲义 §7）
# ══════════════════════════════════════════════════════════════════════


class TransientError(Exception):
    """瞬时错误：可重试（网络抖动、API 限流、超时等）。"""
    pass


class PermanentError(Exception):
    """永久错误：不可重试，需重规划（文件不存在、语法错误、权限拒绝等）。"""
    pass


def with_retry(
    fn: Callable[[], T],
    max_tries: int = 3,
    base: float = 0.5,
) -> T | None:
    """带指数退避的重试包装器。

    Args:
        fn: 要执行的函数（无参数 callable）
        max_tries: 最大尝试次数（含首次）
        base: 退避基数（秒），第 k 次重试等待 base * 2^k

    Returns:
        成功时返回 fn() 的结果；TransientError 超限返回 None。
        PermanentError 直接向上抛出（不重试）。

    用法：
        result = with_retry(lambda: api_call(), max_tries=3)
        if result is None:
            # 重试耗尽，走 blocked 逻辑
    """
    last_error: Exception | None = None
    for k in range(max_tries):
        try:
            return fn()
        except TransientError as e:
            last_error = e
            if k < max_tries - 1:
                wait = base * (2 ** k)
                time.sleep(wait)
        # PermanentError 直接向上抛，不重试
    return None


# ══════════════════════════════════════════════════════════════════════
# 状态标记
# ══════════════════════════════════════════════════════════════════════

_STATUS_MARKS: dict[str, str] = {
    "pending":     "[ ]",
    "in_progress": "[~]",
    "completed":   "[x]",
    "blocked":     "[!]",
}

VALID_STATUSES = frozenset(_STATUS_MARKS)

# 反思上限（讲义 §6）：同一子任务最多反思次数，避免无限套娃
MAX_REFLECTS = 3
# 重试上限：同一子任务最多连续 TransientError 次数
MAX_RETRIES = 3


# ══════════════════════════════════════════════════════════════════════
# TodoList
# ══════════════════════════════════════════════════════════════════════

class TodoList:
    """有序任务清单，支持状态流转、重规划、反思与错误恢复。

    用法：
        t = TodoList()
        t.write(["盘点项目结构", "分析依赖", "生成 README"])
        t.update(1, "in_progress")
        t.update(1, "completed")
        t.update(2, "in_progress")

        # 遇到永久错误 → block + 插入修复子任务
        t.block(2, "缺少 requirements.txt")
        t.insert("先补全 requirements.txt 再分析依赖")

        # 反思机制
        if t.can_reflect(3):
            t.bump_reflect(3)
            # ... 让模型重新审视第 3 步的结果 ...

        print(t.render())
        if t.all_done():
            print("全部完成")
    """

    def __init__(self) -> None:
        self.items: list[dict] = []

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _get(self, id: int) -> dict | None:
        """按 id 获取任务项（id 从 1 开始）。"""
        for it in self.items:
            if it["id"] == id:
                return it
        return None

    def _ensure_meta(self, it: dict) -> None:
        """确保任务项有元数据字段。"""
        it.setdefault("retries", 0)
        it.setdefault("reflects", 0)
        it.setdefault("blocked_reason", "")
        it.setdefault("last_error", "")

    # ------------------------------------------------------------------
    # 写操作
    # ------------------------------------------------------------------

    def write(self, texts: list[str]) -> None:
        """一次性写下分解后的清单（覆盖旧清单）。"""
        self.items = [
            {"id": i + 1, "text": text, "status": "pending",
             "retries": 0, "reflects": 0, "blocked_reason": "", "last_error": ""}
            for i, text in enumerate(texts)
        ]

    def update(self, id: int, status: str) -> bool:
        """更新第 id 项的状态。

        Returns:
            True 表示更新成功，False 表示未找到该 id 或状态非法。
        """
        if status not in VALID_STATUSES:
            return False
        it = self._get(id)
        if it is None:
            return False
        self._ensure_meta(it)

        # 状态流转：completed 时重置重试计数；blocked 保持阻塞状态
        if status == "completed":
            it["retries"] = 0
            it["last_error"] = ""
        elif status == "blocked":
            pass  # 保持已有的 blocked_reason（由 block() 方法设置）
        elif status == "in_progress":
            it["retries"] = 0   # 重新开始时重置重试计数

        it["status"] = status
        return True

    def insert(self, text: str, status: str = "pending") -> int:
        """重规划时插入新子任务。

        Returns:
            新任务的 id。
        """
        if status not in VALID_STATUSES:
            status = "pending"
        new_id = self.items[-1]["id"] + 1 if self.items else 1
        self.items.append({"id": new_id, "text": text, "status": status,
                           "retries": 0, "reflects": 0, "blocked_reason": "", "last_error": ""})
        return new_id

    # ------------------------------------------------------------------
    # 错误恢复操作（讲义 §7）
    # ------------------------------------------------------------------

    def block(self, id: int, reason: str) -> bool:
        """将子任务标记为 blocked 并记录原因。

        永久失败或重试耗尽时调用。Agent 应继续推进其他 pending 任务，
        而不是因为一个子任务卡住就整体失败。

        Returns:
            True 表示成功标记，False 表示未找到该 id。
        """
        it = self._get(id)
        if it is None:
            return False
        self._ensure_meta(it)
        it["status"] = "blocked"
        it["blocked_reason"] = reason
        return True

    def record_error(self, id: int, error: str) -> None:
        """记录子任务的最后一次错误信息。"""
        it = self._get(id)
        if it is not None:
            self._ensure_meta(it)
            it["last_error"] = error

    def bump_retry(self, id: int) -> bool:
        """递增重试计数。返回 True 表示仍可重试，False 表示已达上限。"""
        it = self._get(id)
        if it is None:
            return False
        self._ensure_meta(it)
        it["retries"] += 1
        return it["retries"] < MAX_RETRIES

    def retries_left(self, id: int) -> int:
        """返回剩余重试次数。"""
        it = self._get(id)
        if it is None:
            return 0
        self._ensure_meta(it)
        return max(0, MAX_RETRIES - it["retries"])

    # ------------------------------------------------------------------
    # 反思操作（讲义 §6）
    # ------------------------------------------------------------------

    def can_reflect(self, id: int) -> bool:
        """是否还能对第 id 项进行反思（未达上限）。"""
        it = self._get(id)
        if it is None:
            return False
        self._ensure_meta(it)
        return it["reflects"] < MAX_REFLECTS

    def bump_reflect(self, id: int) -> bool:
        """递增反思计数。返回 True 表示仍可反思，False 表示已达上限。"""
        it = self._get(id)
        if it is None:
            return False
        self._ensure_meta(it)
        it["reflects"] += 1
        return it["reflects"] < MAX_REFLECTS

    def reflects_used(self, id: int) -> int:
        """返回已使用的反思次数。"""
        it = self._get(id)
        if it is None:
            return 0
        self._ensure_meta(it)
        return it["reflects"]

    def reflects_left(self, id: int) -> int:
        """返回剩余反思次数。"""
        it = self._get(id)
        if it is None:
            return 0
        self._ensure_meta(it)
        return max(0, MAX_REFLECTS - it["reflects"])

    # ------------------------------------------------------------------
    # 读操作
    # ------------------------------------------------------------------

    def render(self) -> str:
        """渲染为可注入上下文的文本。

        blocked 项附带原因说明，便于模型判断是否需要先解决阻塞。
        """
        if not self.items:
            return "[任务清单为空]"
        lines: list[str] = []
        for it in self.items:
            mark = _STATUS_MARKS.get(it["status"], "[?]")
            line = f"{mark} {it['id']} {it['text']}"
            # blocked 项附加原因和重试信息
            if it["status"] == "blocked":
                reason = it.get("blocked_reason", "")
                retries = it.get("retries", 0)
                extra = f" — {reason}" if reason else ""
                if retries > 0:
                    extra += f"（已重试 {retries} 次）"
                line += extra
            # 显示剩余反思次数
            reflects = it.get("reflects", 0)
            if reflects > 0:
                line += f" [已反思 {reflects}/{MAX_REFLECTS}]"
            lines.append(line)
        return "\n".join(lines)

    def all_done(self) -> bool:
        """是否所有任务均已完成。"""
        return bool(self.items) and all(
            it["status"] == "completed" for it in self.items
        )

    def summary(self) -> str:
        """一行摘要：已完成 / 进行中 / 待处理 / 阻塞数量。"""
        done = sum(1 for it in self.items if it["status"] == "completed")
        active = sum(1 for it in self.items if it["status"] == "in_progress")
        blocked = sum(1 for it in self.items if it["status"] == "blocked")
        pending = sum(1 for it in self.items if it["status"] == "pending")
        return (
            f"任务总计 {len(self.items)}：{done} 已完成, {active} 进行中, "
            f"{pending} 待处理, {blocked} 阻塞"
        )

    def next_pending(self) -> int | None:
        """返回下一个待处理任务的 id，没有则返回 None。"""
        for it in self.items:
            if it["status"] in ("pending",):
                return it["id"]
        return None

    def active_tasks(self) -> list[int]:
        """返回所有未完成任务的 id 列表。"""
        return [it["id"] for it in self.items if it["status"] != "completed"]

    # ------------------------------------------------------------------
    # 序列化（会话持久化用）
    # ------------------------------------------------------------------

    def to_dict(self) -> list[dict]:
        """导出为可 JSON 序列化的列表。"""
        return list(self.items)

    def snapshot_status(self) -> str:
        """返回当前状态的快照字符串，用于跨轮比较检测进展。"""
        return "|".join(f"{it['id']}:{it['status']}" for it in self.items)

    @classmethod
    def from_dict(cls, data: list[dict]) -> "TodoList":
        """从 JSON 数据恢复。"""
        t = cls()
        t.items = list(data)
        return t


# ══════════════════════════════════════════════════════════════════════
# 无进展 / 绕圈检测（讲义 §8.2）
# ══════════════════════════════════════════════════════════════════════

# 连续无 todo 推进的最大步数，超过则判定为 stalled
MAX_STALL_STEPS = 5
# 同一 (工具, 参数) 在滑动窗口内重复的次数阈值
LOOP_WINDOW = 6
LOOP_REPEAT = 3


class LoopDetector:
    """检测 Agent 是否陷入死循环或无进展停滞。

    两种检测模式：
      1. 绕圈检测：同一 (工具名 + 参数) 在最近 N 步内重复 ≥M 次
      2. 停滞检测：连续 K 步没有 todo 状态变化
    """

    def __init__(self) -> None:
        self._history: list[str] = []           # 最近 LOOP_WINDOW 步的 (tool_name, args_key)
        self._stall_steps: int = 0              # 连续无 todo 进展的步数
        self._last_todo_snapshot: str = ""      # 上一轮的 todo 快照

    def feed(self, tool_name: str, tool_args: dict, todo_snapshot: str) -> None:
        """记录一步工具调用，更新检测状态。

        Args:
            tool_name: 调用的工具名
            tool_args: 工具参数
            todo_snapshot: 当前 todo 状态快照（来自 TodoList.snapshot_status()）
        """
        # 绕圈检测：记录归一化的 (tool, args_key)
        args_key = _normalize_args(tool_args)
        self._history.append(f"{tool_name}|{args_key}")
        if len(self._history) > LOOP_WINDOW:
            self._history.pop(0)

        # 停滞检测：比较 todo 是否变化
        if todo_snapshot and todo_snapshot == self._last_todo_snapshot:
            self._stall_steps += 1
        else:
            self._stall_steps = 0
        self._last_todo_snapshot = todo_snapshot

    def is_looping(self) -> str | None:
        """检测是否绕圈。返回描述字符串或 None。"""
        if len(self._history) < LOOP_REPEAT:
            return None
        recent = self._history[-LOOP_WINDOW:] if len(self._history) >= LOOP_WINDOW else self._history
        counts: dict[str, int] = {}
        for entry in recent:
            counts[entry] = counts.get(entry, 0) + 1
        for entry, count in counts.items():
            if count >= LOOP_REPEAT:
                tool_name = entry.split("|")[0]
                return f"检测到绕圈：工具 {tool_name} 在最近 {len(recent)} 步中重复了 {count} 次。建议换一种策略或向用户求助。"
        return None

    def is_stalled(self) -> str | None:
        """检测是否停滞。返回描述字符串或 None。"""
        if self._stall_steps >= MAX_STALL_STEPS:
            return f"检测到停滞：连续 {self._stall_steps} 步没有任务清单进展。建议检查当前策略是否有效，或标记当前子任务为 blocked。"
        return None

    def reset(self) -> None:
        """重置检测状态（重规划时调用）。"""
        self._history.clear()
        self._stall_steps = 0
        self._last_todo_snapshot = ""


def _normalize_args(args: dict) -> str:
    """规范化参数字典为可比较的字符串。"""
    if not args:
        return "{}"
    # 排序 key，截断 value 到 80 字符避免长路径干扰匹配
    items = sorted(args.items(), key=lambda x: x[0])
    norm = []
    for k, v in items:
        s = str(v)
        if len(s) > 80:
            s = s[:80] + "…"
        norm.append(f"{k}={s}")
    return ";".join(norm)
