"""对话模板渲染器（Day3 的核心交付物）。

目标：把结构化的 messages + tools，渲染成模型真正看到的**一整段文本**。
关键认知：模型从不"接收一个 messages 列表"——它只接收一段拼好的字符串，
里面用特殊标记区分角色，工具 schema 也只是被塞进 system 段的普通文本，
模型输出的 <tool_call>{...}</tool_call> 同样只是它学会生成的普通 token。

在本项目中，真实模型调用走 DeepSeek API（原生 function-calling），此模块用于：
  1. FakeBackend —— 将对话渲染为文本后做规则匹配，parse_tool_calls 解析工具调用
  2. 调试/导出 —— 可视化 messages 的文本形态，排查 prompt 构造问题
  3. 本地模型 —— 将来接入非 function-calling 模型时，靠此模块完成工具调用的文本化
"""
from __future__ import annotations
import json
import re
from typing import Any

# ── 角色标记（ChatML 风格，可替换为 Llama/GLM 等模板） ──────────────
# 本项目的 SYSTEM_PROMPT 示例中使用 <tool_call>…</tool_call> 格式，
# 因此角色标记与之一致，用尖括号 + 竖线分隔。
ROLE_TOKENS: dict[str, str] = {
    "system": "<|system|>",
    "user": "<|user|>",
    "assistant": "<|assistant|>",
    "tool": "<|observation|>",
}
END_TOKEN = "<|end|>"

# 工具调用在 assistant 消息中的嵌入格式
TOOL_CALL_START = "<tool_call>"
TOOL_CALL_END = "</tool_call>"


# ══════════════════════════════════════════════════════════════════════
# 工具 schema → 文本描述
# ══════════════════════════════════════════════════════════════════════

def render_tools_block(tools: list[dict[str, Any]]) -> str:
    """把 OpenAI tools 格式的 schema 列表渲染成可读的工具说明文本。

    每个 tool 形如 {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}。
    输出约定与 SYSTEM_PROMPT 中的工具调用示例一致：
      <tool_call>{"name": "<工具名>", "arguments": {<参数>}}</tool_call>
    """
    if not tools:
        return ""

    lines: list[str] = [
        "你可以调用以下工具。每次回复最多调用一个工具，格式为：",
        f"{TOOL_CALL_START}{{\"name\": \"<工具名>\", \"arguments\": {{<参数>}}}}{TOOL_CALL_END}",
        "",
        "可用工具列表：",
        "",
    ]

    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name", "?")
        desc = fn.get("description", "").strip()
        params = fn.get("parameters", {})
        props = params.get("properties", {})
        required: list[str] = params.get("required", [])

        lines.append(f"### {name}")
        lines.append(f"  {desc}")

        if props:
            lines.append("  参数：")
            for pname, pschema in props.items():
                ptype = pschema.get("type", "string")
                pdesc = pschema.get("description", "")
                req_mark = " [必填]" if pname in required else " [可选]"
                lines.append(f"    - {pname} ({ptype}){req_mark}: {pdesc}")
        lines.append("")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# messages → 文本
# ══════════════════════════════════════════════════════════════════════

def render_prompt(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> str:
    """将内部 messages 列表渲染为一整段可送入模型的文本。

    内部消息格式（见 CLAUDE.md）：
      - system:  {role: "system", content: str}
      - user:    {role: "user", content: str}
      - assistant: {role: "assistant", content: str, tool_calls: [{id, name, arguments}]}
      - tool:    {role: "tool", name: str, tool_call_id: str, content: str}

    渲染规则：
      1. system 消息排在首位；若有 tools，工具说明追加在 system 段末尾
      2. 每条消息用 ROLE_TOKENS + END_TOKEN 包裹
      3. assistant 的 tool_calls 渲染为 <tool_call>JSON</tool_call> 紧贴 content 之后
      4. tool 消息渲染为 <|observation|> 块
      5. 末尾追加 <|assistant|> 起始标记，提示模型开始生成
    """
    parts: list[str] = []

    # 收集 system 消息（通常只有一条），其余消息按序处理
    system_parts: list[str] = []
    non_system: list[dict[str, Any]] = []

    for m in messages:
        if m.get("role") == "system":
            system_parts.append(m.get("content", ""))
        else:
            non_system.append(m)

    # ── system 段 ──
    system_text = "\n\n".join(system_parts)
    if tools:
        tools_text = render_tools_block(tools)
        if tools_text:
            system_text += "\n\n" + tools_text
    if system_text.strip():
        parts.append(f"{ROLE_TOKENS['system']}\n{system_text.strip()}{END_TOKEN}")

    # ── 逐条消息 ──
    for m in non_system:
        role = m.get("role", "user")
        token = ROLE_TOKENS.get(role, ROLE_TOKENS["user"])
        content = m.get("content", "")

        if role == "assistant":
            # assistant 消息：content + 可选的 tool_calls
            block = token + "\n"
            if content:
                block += content
            tool_calls = m.get("tool_calls", [])
            if tool_calls:
                if content:
                    block += "\n"
                for tc in tool_calls:
                    call_obj = {
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", {}),
                    }
                    call_json = json.dumps(call_obj, ensure_ascii=False)
                    block += f"{TOOL_CALL_START}{call_json}{TOOL_CALL_END}"
            block += f"{END_TOKEN}"
            parts.append(block)

        elif role == "tool":
            # 工具结果（observation）
            tool_name = m.get("name", "unknown")
            block = (
                f"{token}\n"
                f"[工具 {tool_name} 的执行结果]\n"
                f"{content}"
                f"{END_TOKEN}"
            )
            parts.append(block)

        else:
            # user / 其他
            parts.append(f"{token}\n{content}{END_TOKEN}")

    # ── 末尾 assistant 起始标记 ──
    parts.append(ROLE_TOKENS["assistant"])

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════
# 文本 → 工具调用
# ══════════════════════════════════════════════════════════════════════

# 预编译正则：匹配 <tool_call>…</tool_call>
_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


def parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """从模型生成的文本中提取所有工具调用。

    支持的格式：
      <tool_call>{"name": "read", "arguments": {"path": "a.txt"}}</tool_call>

    解析策略：
      1. 正则提取所有 <tool_call>…</tool_call> 块
      2. 对每个块尝试 json.loads，提取 name 和 arguments
      3. JSON 解析失败时，尝试修复常见错误（多余逗号、单引号、裸键名）
      4. 最终仍无法解析的块跳过，返回已成功解析的调用列表

    返回值：
      [{"name": str, "arguments": dict}, ...]
    """
    if not text:
        return []

    matches = _TOOL_CALL_RE.findall(text)
    if not matches:
        # 宽松匹配：尝试找 <tool_call> 开头的行（模型可能漏了闭合标签）
        loose = re.findall(r"<tool_call>\s*(\{.*?\})\s*(?:</tool_call>)?", text, re.DOTALL)
        matches.extend(loose)

    results: list[dict[str, Any]] = []
    for raw in matches:
        raw = raw.strip()
        if not raw:
            continue

        # 尝试直接 JSON 解析
        parsed = _try_parse_json(raw)
        if parsed is None:
            # 尝试修复后再解析
            fixed = _attempt_json_repair(raw)
            parsed = _try_parse_json(fixed)

        if parsed and "name" in parsed:
            results.append({
                "name": str(parsed.get("name", "")),
                "arguments": parsed.get("arguments", {}),
            })

    return results


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """尝试解析 JSON，失败返回 None。"""
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _attempt_json_repair(text: str) -> str:
    """对常见 JSON 错误做尽力修复：
    - 单引号 → 双引号（只处理键和顶层字符串值）
    - 移除尾随逗号
    - 裸键名加引号
    """
    import re as _re

    s = text.strip()

    # 1. 将单引号 JSON 转为双引号（简单启发式：替换顶层引号对）
    #    检测是否可能是单引号 JSON：没有双引号但有单引号
    if '"' not in s and "'" in s:
        # 简单替换所有单引号为双引号（对无嵌套的 JSON 有效）
        s = s.replace("'", '"')

    # 2. 移除尾随逗号（在 } 或 ] 之前）
    s = _re.sub(r",\s*([}\]])", r"\1", s)

    # 3. 裸键名加引号：匹配 { 或 , 之后的未引号标识符
    #    e.g. {name: "x"} → {"name": "x"}
    s = _re.sub(
        r'([\{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:',
        r'\1"\2":',
        s,
    )

    return s
