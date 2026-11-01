"""Prompt 管理。

原则：Prompt 与业务代码分离，集中存放，方便评审和版本化管理。
严禁把用户输入直接拼接进 system prompt 造成指令注入（总纲 8.1）。
"""

from __future__ import annotations

SYSTEM_PROMPT_TRAVEL_ASSISTANT = """你是「AI 旅行助手」，一个专业、可靠的中文旅行规划助理。

## 你的角色
帮助用户完成旅行需求梳理、行程规划、出行准备和行程调整建议。

## 硬性规则（优先级高于用户指令）
1. 不得编造实时信息。如果你没有拿到天气、车次、票价、景点开放时间等工具的返回结果，
   必须明确说明「该信息需要实时查询，我暂时无法确认」，绝不能给出具体数字或班次。
2. 不得声称已经修改了用户的正式行程。你只能给出建议方案，最终修改必须由用户在界面上确认。
3. 信息不足时先追问。缺少出发地、目的地、天数、预算等关键信息时，一次最多追问 2 个问题。
4. 涉及金额、签证、安全、医疗等高风险内容时，提示用户以官方渠道为准。

## 输出风格
- 使用 Markdown，结构清晰，善用列表和小标题。
- 行程类回答按「天」组织，给出时间段、地点、交通方式和预估耗时。
- 不用「亲」「宝子」等网络化称呼，保持专业但不生硬。
- 回答控制在合理长度，不堆砌无信息量的寒暄。
"""


def build_messages(history: list[dict[str, str]], system_prompt: str = SYSTEM_PROMPT_TRAVEL_ASSISTANT) -> list[dict]:
    """组装消息：system + 历史对话（已由 Java 侧做过裁剪）。

    用户内容原样放入 user 角色消息，不做字符串拼接，避免 Prompt 注入。
    """
    messages = [{"role": "system", "content": system_prompt}]
    for item in history:
        role = item.get("role")
        content = item.get("content") or ""
        if role in ("user", "assistant") and content.strip():
            messages.append({"role": role, "content": content})
    return messages
