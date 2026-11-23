"""提示词与校验器同源检查（BUG-008 / BUG-014 的结构性防线）。

两个缺陷的形态完全一致：Java 校验器已经会拒某类输出，Python 提示词里却从没写过，
模型只能靠猜，猜错就整份 `SCHEMA_VIOLATION`。修一次提示词能救一次实跑，
但下一次谁再往校验器里加一条规则，同样的坑会再踩一遍。

这里把「规则」变成可枚举、可交叉验证的对象（`contracts/travel-plan/plan_rules_v1.json`），
并从四个方向盯住：

1. 提示词里那段必须**就是**渲染结果（防止绕过 JSON 直接手改提示词）；
2. 清单里声明的每个 code，校验器代码里都必须真的会抛（防止规则只写在文档里）；
3. 校验器代码里出现的每个 code，清单必须有对应条目 —— **这条直接对应 BUG-014**；
4. 每个 code 都要有一条以它开头的反例（否则等于没被证明，对应 BUG-014 当时 CI 全绿）。

第 3、4 条会让「加规则不给提示词」这件事在 CI 阶段就失败，而不是等到实跑 20 次才发现 19/20。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.llm.prompts import PLAN_SYSTEM_PROMPT
from app.plan.rules import RULES_FILE, load_rules, render_rules_section, validator_codes

REPO_ROOT = Path(__file__).resolve().parents[2]
JAVA_VALIDATOR = (
    REPO_ROOT
    / "backend-java/src/main/java/com/aitravel/modules/plan/validation/PlanSchemaValidator.java"
)
JAVA_MUTANTS = REPO_ROOT / "backend-java/src/test/java/com/aitravel/support/PlanMutants.java"

# 校验器里真正会抛出的 code：new PlanViolation("S-6", ...)
JAVA_CODE = re.compile(r'new PlanViolation\("([A-Z]+-\d+)"')
# 反例命名约定：m("S-6 ...", ...)
MUTANT_NAME = re.compile(r'm\("([A-Z]+-\d+) ')


def _java_source(path: Path) -> str:
    assert path.exists(), f"找不到 Java 源码：{path}（跨语言检查依赖它，路径变了要同步改这里）"
    return path.read_text(encoding="utf-8")


def test_prompt_uses_rendered_rules_verbatim():
    """提示词里的硬性规则段必须等于渲染结果，手写副本一律算错。"""
    rendered = render_rules_section()
    assert rendered in PLAN_SYSTEM_PROMPT, (
        "PLAN_SYSTEM_PROMPT 与 plan_rules_v1.json 的渲染结果不一致："
        "要么有人绕过 JSON 手改了提示词，要么提示词没有用 render_rules_section() 拼接"
    )
    assert PLAN_SYSTEM_PROMPT.count("## 硬性规则") == 1, "硬性规则段出现了多次，可能残留了手写副本"


def test_rules_are_complete():
    """清单自身：id 唯一、有编号、有文本、有 why。"""
    doc = load_rules()
    ids = [rule["id"] for rule in doc["rules"]]
    assert len(ids) == len(set(ids)), f"规则 id 重复：{ids}"
    for rule in doc["rules"]:
        assert rule.get("prompt_lines"), f"规则 {rule['id']} 没有 prompt_lines"
        assert rule.get("number"), f"规则 {rule['id']} 没有 number"
        assert rule.get("why"), f"规则 {rule['id']} 没有 why：以后没人知道为什么这么写"
        assert rule.get("enforced_by") in {"validator", "schema", "prompt"}, (
            f"规则 {rule['id']} 的 enforced_by 取值非法：{rule.get('enforced_by')}"
        )


def test_every_declared_code_is_really_raised_by_java():
    """清单说「校验器会拒」的每个 code，Java 里必须真的构造过这个 code。"""
    raised = set(JAVA_CODE.findall(_java_source(JAVA_VALIDATOR)))
    declared = validator_codes()
    missing = sorted(declared - raised)
    assert not missing, (
        f"这些 code 在 {RULES_FILE} 里声明了，但 PlanSchemaValidator 从没抛出过：{missing}。"
        "要么规则早已失效（提示词在约束一件没人检查的事），要么 code 写错了"
    )


def test_no_java_rule_left_out_of_the_contract():
    """反向：校验器抛出的每个 code，清单里都必须有对应条目（BUG-014 的直接解药）。"""
    raised = set(JAVA_CODE.findall(_java_source(JAVA_VALIDATOR)))
    declared = set(validator_codes())
    orphan = sorted(raised - declared)
    assert not orphan, (
        f"这些校验规则在 Java 里执行，但 {RULES_FILE} 没有对应条目：{orphan}。"
        "提示词因此不会提到它们 —— 这就是 BUG-014 的成因（模型写出 fromDayIndex==toDayIndex 才发现）。"
        "请先在清单里补一条（含 prompt_lines），再到 PlanMutants 里补一条以 code 开头的反例"
    )


def test_every_code_has_a_mutant():
    """每个 code 都要有一条能触发它的反例，否则这条规则从未被证明过。"""
    mutants = set(MUTANT_NAME.findall(_java_source(JAVA_MUTANTS)))
    missing = sorted(set(validator_codes()) - mutants)
    assert not missing, (
        f"这些 code 没有任何畸形样例：{missing}。"
        "没有反例的规则等于没被证明 —— 当年 S-6 就是这样一路绿到实跑才炸"
    )


def test_contract_file_is_shipped_with_schema():
    """规则清单必须与两份 Schema 放在同一目录，Java 打包时一并进 classpath。"""
    doc = load_rules()
    assert doc.get("section_title", "").startswith("## "), "section_title 必须是 Markdown 标题，否则提示词层级会乱"
    assert (REPO_ROOT / "contracts" / "travel-plan" / RULES_FILE).exists()
