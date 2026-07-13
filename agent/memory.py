"""Agent 记忆系统（Day11，讲义 §6）。

两层记忆：
  1. Memory — 纯文本追加，适合约定/偏好/决策等自然语言条目
  2. KVMemory — 结构化 KV，支持按 key 覆盖与删除，避免纯文本的陈旧膨胀

把"跨会话仍成立的信息"写到会话之外的文件，下次会话读得回。
"""
from __future__ import annotations
import json
from pathlib import Path


class Memory:
    def __init__(self, path: str = "MEMORY.md"):
        self.path = Path(path)

    def write(self, note: str) -> None:
        """写入一条记忆（追加落盘 = 持久化）。"""
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("- " + note.strip() + "\n")

    def recall(self, query: str = "") -> str:
        """召回：最简版本 = 读回全部（策略 A）。"""
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")


class KVMemory:
    """结构化 KV 记忆，支持按 key 覆盖（更新）与删除（遗忘）。

    解决纯文本 `Memory` 的两大痛点（讲义 §4.3/§4.4）：
      - 陈旧：同一主题多次记录，旧条目仍在，模型可能读到矛盾的旧信息
      - 膨胀：只增不改，文件随时间增长，浪费上下文窗口
    """

    def __init__(self, path: str = "memory.json"):
        self.path = Path(path)
        self.data: dict[str, str] = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {}
        )

    def _save(self) -> None:
        """持久化到 JSON 文件。"""
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def remember(self, key: str, value: str) -> None:
        """写入或更新一条记忆（同 key 覆盖 = 自动修正陈旧信息）。"""
        self.data[key] = value
        self._save()

    def forget(self, key: str) -> bool:
        """删除一条记忆，返回是否确实删除了。"""
        if key in self.data:
            del self.data[key]
            self._save()
            return True
        return False

    def recall(self) -> str:
        """召回全部 KV 记忆，渲染为 Markdown 列表，供注入 system prompt。"""
        if not self.data:
            return ""
        return "\n".join(f"- **{k}**：{v}" for k, v in self.data.items())
