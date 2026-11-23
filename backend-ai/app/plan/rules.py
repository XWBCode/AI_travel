"""跨字段硬约束的统一来源与提示词渲染。

背景（BUG-008 / BUG-014）：校验器在 Java、提示词在 Python，两边各写各的，
结果是「校验器已经会拒的规则，提示词从没提过」——模型只能靠猜，猜错就被整份拒掉。
这类缺陷在日志里表现为 `plan_rejected: SCHEMA_VIOLATION`，而根因在提示词。

解法：规则清单落在 `contracts/travel-plan/plan_rules_v1.json`（与 Schema 同目录，
Java 侧也读它）。提示词的「硬性规则」整段由本模块渲染，不再手写；
`tests/test_plan_prompt_sync.py` 会在三个方向上把关：

1. 渲染出来的每一段都必须真的出现在 `PLAN_SYSTEM_PROMPT` 里（防止有人绕过 JSON 直接改提示词）；
2. 每条 `codes` 里的校验器 code 都必须在 `PlanSchemaValidator` 里出现过（防止规则只存在于文档）；
3. 校验器里出现的每个 code 都必须被某条规则覆盖（防止新增校验规则却不给提示词——就是 BUG-014）。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.plan.contract import contract_dir, load_schema

RULES_FILE = "plan_rules_v1.json"


@lru_cache(maxsize=2)
def load_rules() -> dict[str, Any]:
    """读取规则清单。文件缺失或格式不对时直接抛——宁可服务起不来，也不要在规则缺失下跑。"""
    doc = load_schema(RULES_FILE)
    rules = doc.get("rules") or []
    if not rules:
        raise ValueError(f"{RULES_FILE} 里没有任何规则，提示词会缺失全部硬约束")
    seen: set[str] = set()
    for rule in rules:
        rule_id = rule.get("id")
        if not rule_id:
            raise ValueError(f"{RULES_FILE} 存在缺少 id 的规则：{rule}")
        if rule_id in seen:
            raise ValueError(f"{RULES_FILE} 中规则 id 重复：{rule_id}")
        seen.add(rule_id)
        if not rule.get("prompt_lines"):
            raise ValueError(f"规则 {rule_id} 没有 prompt_lines，提示词会漏掉这条约束")
        if not rule.get("number"):
            raise ValueError(f"规则 {rule_id} 没有 number，渲染出的编号会错位")
    return doc


def render_rules_section(doc: dict[str, Any] | None = None) -> str:
    """把规则清单渲染成提示词里的「硬性规则」段落。

    同一 number 下的多条规则共用一个小节编号（例：编号 2 下有三条不同约束），
    只有该编号的第一条带「2. 」前缀，其余继续缩进，读起来仍是一段。
    """
    doc = doc or load_rules()
    out: list[str] = [doc["section_title"]]
    last_number: str | None = None
    for rule in doc["rules"]:
        number = rule["number"]
        lines = rule["prompt_lines"]
        # 「4.1」这类二级编号自带小数点，后面只留空格，避免渲染成「4.1. 」
        separator = " " if "." in number else ". "
        prefix = f"{number}{separator}" if number != last_number else "   "
        out.append(prefix + lines[0])
        out.extend("   " + line for line in lines[1:])
        last_number = number
    return "\n".join(out)


def validator_codes(doc: dict[str, Any] | None = None) -> set[str]:
    """规则清单声明由 Java 校验器强制执行的 code 集合（S-1~S-6、Q-12 等）。"""
    doc = doc or load_rules()
    codes: set[str] = set()
    for rule in doc["rules"]:
        codes.update(rule.get("codes") or [])
    return codes


def rules_by_code(doc: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """code -> 覆盖它的规则条目，供日志与排查用（例：S-6 对应提示词第 7 条）。"""
    doc = doc or load_rules()
    mapping: dict[str, dict[str, Any]] = {}
    for rule in doc["rules"]:
        for code in rule.get("codes") or []:
            mapping[code] = rule
    return mapping


__all__ = [
    "RULES_FILE",
    "contract_dir",
    "load_rules",
    "render_rules_section",
    "validator_codes",
    "rules_by_code",
]
