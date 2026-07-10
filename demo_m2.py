from backend.client import DeepSeekBackend
from agent.prompts import SYSTEM_PROMPT

tools = [{
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "返回当前时间。用户询问现在几点、当前时间时使用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]

backend = DeepSeekBackend()
resp2 = backend.chat(
    [{"role": "system", "content": SYSTEM_PROMPT},
     {"role": "user", "content": "请分析 https://github.com/ZJU-Turing/TuringCourses 这一仓库的结构"}],
    tools=tools,
)
print(resp2)