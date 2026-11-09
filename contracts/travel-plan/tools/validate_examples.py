#!/usr/bin/env python
"""TravelPlan v1 契约自检。

做三件事：
1. 两份 Schema 自身的语法合法性（meta-schema）
2. 合规样例必须同时通过 draft 与 stored 两份 Schema
3. examples/invalid_cases.json 中的畸形样例必须全部被拒绝，且错误定位到预期字段

用法：python contracts/travel-plan/tools/validate_examples.py
依赖：pip install jsonschema
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

BASE = Path(__file__).resolve().parent.parent


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def apply_mutations(doc, mutations):
    for mutation in mutations:
        node = doc
        path = mutation["path"]
        for key in path[:-1]:
            node = node[key]
        if mutation.get("delete"):
            del node[path[-1]]
        else:
            node[path[-1]] = mutation["value"]
    return doc


def collect_fact_fields(node, acc):
    if isinstance(node, dict):
        if "verified" in node and "display" in node and "source" in node:
            acc.append(node)
            return
        for value in node.values():
            collect_fact_fields(value, acc)
    elif isinstance(node, list):
        for value in node:
            collect_fact_fields(value, acc)


def format_path(error) -> str:
    return "/".join(str(p) for p in error.path) or "<root>"


def report_failure(title: str, errors) -> None:
    print(f"FAIL {title}")
    for error in errors[:5]:
        print(f"  at /{format_path(error)}: {error.message}")


def main() -> int:
    draft_schema = load(BASE / "travel_plan_draft_v1.json")
    stored_schema = load(BASE / "travel_plan_v1.json")
    sample = load(BASE / "examples" / "draft.valid.json")
    cases = load(BASE / "examples" / "invalid_cases.json")

    for name, schema in (("draft", draft_schema), ("stored", stored_schema)):
        Draft202012Validator.check_schema(schema)
        print(f"schema {name}: OK (meta-schema)")

    checker = FormatChecker()
    draft_validator = Draft202012Validator(draft_schema, format_checker=checker)
    stored_validator = Draft202012Validator(stored_schema, format_checker=checker)

    # 1. 合规样例 -> draft
    errors = sorted(draft_validator.iter_errors(sample), key=lambda e: list(e.path))
    if errors:
        report_failure("valid sample vs draft schema", errors)
        return 1
    print("valid sample -> draft: PASS")

    # 2. 合规样例 -> stored（补落库后才有的字段）
    stored_sample = copy.deepcopy(sample)
    stored_sample["planId"] = "1730000000000000001"
    stored_sample["version"] = 0
    errors = sorted(stored_validator.iter_errors(stored_sample), key=lambda e: list(e.path))
    if errors:
        report_failure("valid sample vs stored schema", errors)
        return 1
    print("valid sample -> stored: PASS")

    # 3. unverifiedFieldCount 必须与实例数一致（AC-08 口径自检）
    fields: list[dict] = []
    collect_fact_fields(sample, fields)
    unverified = sum(1 for field in fields if field.get("verified") is False)
    declared = sample["unverifiedFieldCount"]
    print(
        f"FactField instances={len(fields)}, unverified={unverified}, "
        f"declared={declared}"
    )
    if unverified != declared:
        print("FAIL unverifiedFieldCount 与实例统计不一致")
        return 1

    # 4. 畸形样例必须全部被拒
    rejected = 0
    path_mismatch: list[str] = []
    for case in cases:
        document = copy.deepcopy(sample)
        apply_mutations(document, case["mutations"])
        errors = list(draft_validator.iter_errors(document))
        if not errors:
            print(f"FAIL invalid[{case['name']}]: 未被拒绝 —— {case['why']}")
            continue
        rejected += 1
        expect = case.get("expect")
        if expect and not any(expect in format_path(e) for e in errors):
            path_mismatch.append(
                f"{case['name']}: 期望定位 {expect}，实际 {format_path(errors[0])}"
            )

    print(f"invalid cases: {rejected}/{len(cases)} rejected")
    for item in path_mismatch:
        print(f"  WARN 错误定位偏差 -> {item}")

    return 0 if rejected == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
