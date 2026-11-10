"""TravelPlan 契约的加载与校验。

原则（05 文档 §1）：契约唯一来源是仓库根 `contracts/travel-plan/`，
Python 与 Java 读同一份文件，任何一侧都不得各自维护副本。

本模块只做**结构与字段级**校验。JSON Schema 表达不了的跨字段规则
（dayIndex 连续、itemOrder 唯一、missingFields 与 null 状态对应等，即 08 文档的 S-1~S-6）
由 Java 侧 PlanSchemaValidator 负责，这里不重复实现，避免两套规则漂移。
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

# app/plan/contract.py -> app/plan -> app -> backend-ai -> 仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]

DRAFT_SCHEMA_FILE = "travel_plan_draft_v1.json"
STORED_SCHEMA_FILE = "travel_plan_v1.json"

# FactField 的七个键，缺一或多一都不算（用于统计 unverifiedFieldCount）
FACT_FIELD_KEYS = frozenset({"value", "display", "source", "verified", "fetchedAt", "expiresAt", "note"})

# 自由文本里禁止出现的"数字 + 单位"，与 Schema 的 freeText.not.pattern 保持同一份口径
NUMERIC_UNIT_PATTERN = re.compile(
    r"[0-9]+\s*(元|块钱|块|℃|°C|摄氏度|度|分钟|小时|公里|千米|米|km|KM|车次|班次|班)"
)


def contract_dir() -> Path:
    """契约目录：默认按仓库相对路径推导，可用 CONTRACTS_DIR 覆盖（部署/测试用）。"""
    override = os.getenv("CONTRACTS_DIR")
    return Path(override) if override else REPO_ROOT / "contracts" / "travel-plan"


@lru_cache(maxsize=4)
def load_schema(filename: str) -> dict[str, Any]:
    path = contract_dir() / filename
    if not path.exists():
        raise FileNotFoundError(f"契约文件不存在: {path}（可用 CONTRACTS_DIR 指定目录）")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=4)
def _validator(filename: str) -> Draft202012Validator:
    schema = load_schema(filename)
    # 启动即校验 Schema 自身合法，避免契约被改坏后运行到一半才发现
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_draft(plan: Any, limit: int = 20) -> list[str]:
    """校验计划草稿，返回人类可读的错误列表；空列表代表通过。

    只返回前 limit 条：畸形输出可能一次报出上百条，日志里没有价值。
    """
    errors = sorted(_validator(DRAFT_SCHEMA_FILE).iter_errors(plan), key=lambda e: list(e.absolute_path))
    result: list[str] = []
    for err in errors[:limit]:
        location = "/".join(str(p) for p in err.absolute_path) or "<root>"
        result.append(f"{location}: {err.message}")
    if len(errors) > limit:
        result.append(f"... 其余 {len(errors) - limit} 条已省略")
    return result


def is_fact_field(node: Any) -> bool:
    """判断节点是否是 FactField：键集合完全一致，不允许多也不允许少。"""
    return isinstance(node, dict) and len(node) == len(FACT_FIELD_KEYS) and FACT_FIELD_KEYS.issubset(node.keys())


def count_fact_fields(node: Any) -> int:
    """递归统计计划中 FactField 实例数，用于核对 unverifiedFieldCount。"""
    if isinstance(node, dict):
        if is_fact_field(node):
            return 1
        return sum(count_fact_fields(v) for v in node.values())
    if isinstance(node, list):
        return sum(count_fact_fields(v) for v in node)
    return 0


def scan_free_text(plan: Any, path: str = "") -> list[str]:
    """扫描所有字符串值里的"数字 + 单位"。

    Schema 的 freeText 只能约束被 $ref 引用的字段（risks / aiNote / note / advice），
    `accommodation.suggestion` 与 `title` 是普通 string，属于 Schema 覆盖不到的口子。
    这里做全量兜底，与 Java 侧 WP4 的文本扫描同一口径。
    """
    hits: list[str] = []
    if isinstance(plan, dict):
        for key, value in plan.items():
            hits.extend(scan_free_text(value, f"{path}/{key}" if path else key))
    elif isinstance(plan, list):
        for index, value in enumerate(plan):
            hits.extend(scan_free_text(value, f"{path}/{index}"))
    elif isinstance(plan, str):
        for match in NUMERIC_UNIT_PATTERN.finditer(plan):
            hits.append(f"{path}: 自由文本含具体数值「{match.group(0).strip()}」（{plan[:40]}）")
    return hits


def check_consistency(plan: dict[str, Any]) -> list[str]:
    """Schema 表达不了、但生成侧必须自检的规则。Java 侧会再独立校验一遍。"""
    problems: list[str] = []

    declared = plan.get("unverifiedFieldCount")
    actual = count_fact_fields(plan)
    if declared != actual:
        problems.append(f"unverifiedFieldCount 声明 {declared}，实际 {actual}")

    problems.extend(scan_free_text(plan))

    basic = plan.get("basic") or {}
    missing = set(basic.get("missingFields") or [])
    # 只校验 basic 内的字段；budget/interests 等不在 basic 中的由 Java 侧按 08 文档规则处理
    for field in ("originCity", "destination", "startDate", "endDate", "days", "travelers"):
        if field in basic:
            is_null = basic.get(field) is None
            if is_null and field not in missing:
                problems.append(f"basic.{field} 为 null 但未列入 missingFields")
            if not is_null and field in missing:
                problems.append(f"basic.{field} 有值却仍列入 missingFields")

    destinations = basic.get("destinations") or []
    if destinations and basic.get("destination") != destinations[0]:
        problems.append("basic.destinations[0] 必须等于 basic.destination（Q-01）")

    return problems


def verify(plan: Any) -> list[str]:
    """完整校验：Schema + 一致性自检。返回空列表代表可用。"""
    errors = validate_draft(plan)
    if errors:
        return errors
    if isinstance(plan, dict):
        return check_consistency(plan)
    return []
