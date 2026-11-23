package com.aitravel.modules.plan.validation;

import com.aitravel.support.PlanMutants;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;

import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 规则清单（{@code contracts/travel-plan/plan_rules_v1.json}）与校验器的双向对齐。
 * <p>
 * 这是 BUG-008 与 BUG-014 的结构性防线。两次缺陷的形态完全一样：
 * 校验器已经会拒绝某类输出，但提示词里从没写过这条约束，模型只能靠猜；
 * 更糟的是<b>连一条反例都没有</b>，所以 CI 一直是绿的。
 * <p>
 * 本用例把「规则」钉成一个可枚举的对象，并对每条规则要求：
 * <ol>
 *   <li>提示词里有对应的说明（渲染时由清单生成，空白即失败）；</li>
 *   <li>存在一条以 code 开头的畸形样例，且该样例确实被这个 code 拒绝。</li>
 * </ol>
 * 也就是说，以后谁想加一条校验规则，必须同时给提示词和反例，否则这里直接红。
 */
class PlanRuleCoverageTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String RULES_PATH = "schemas/plan_rules_v1.json";

    private static PlanSchemaValidator validator;
    private static JsonNode rules;

    @BeforeAll
    static void setUp() throws Exception {
        validator = new PlanSchemaValidator(MAPPER);
        try (InputStream in = new ClassPathResource(RULES_PATH).getInputStream()) {
            rules = MAPPER.readTree(in).path("rules");
        }
        assertTrue(rules.isArray() && !rules.isEmpty(),
                () -> RULES_PATH + " 必须存在且包含规则，否则提示词会整段缺失");
    }

    @Test
    @DisplayName("每条校验器规则都有能触发它的反例，且确实由该规则拒绝")
    void everyValidatorRuleIsEnforcedAndReproducible() {
        List<PlanMutants.Mutant> mutants = PlanMutants.all();
        List<String> noCase = new ArrayList<>();
        List<String> wrongCode = new ArrayList<>();

        for (JsonNode rule : rules) {
            String ruleId = rule.path("id").asText();
            for (JsonNode codeNode : rule.path("codes")) {
                String code = codeNode.asText();
                PlanMutants.Mutant mutant = mutants.stream()
                        .filter(m -> m.name().startsWith(code + " "))
                        .findFirst()
                        .orElse(null);
                if (mutant == null) {
                    noCase.add(code + "（规则 " + ruleId + "）");
                    continue;
                }
                ObjectNode node = (ObjectNode) PlanMutants.validDraft();
                mutant.mutator().accept(node);
                boolean hit = validator.validate(node).stream().anyMatch(v -> code.equals(v.code()));
                if (!hit) {
                    wrongCode.add(code + " ← " + mutant.name());
                }
            }
        }

        assertEquals(List.of(), noCase,
                "以下规则没有任何反例，等于从未被证明过：新增规则必须同时在 PlanMutants 里加一条以 code 开头的样例");
        assertEquals(List.of(), wrongCode,
                "以下反例没有命中预期规则，说明校验器并未真正执行该规则（或反例被 Schema 层先行拦下）");
    }

    @Test
    @DisplayName("每条规则都必须有提示词文本，且编号不成倒退")
    void everyRuleCarriesPromptText() {
        for (JsonNode rule : rules) {
            String ruleId = rule.path("id").asText();
            assertFalse(ruleId.isBlank(), "规则缺少 id");
            JsonNode lines = rule.path("prompt_lines");
            assertTrue(lines.isArray() && !lines.isEmpty(),
                    () -> "规则 " + ruleId + " 没有 prompt_lines：提示词会漏掉这条硬约束，模型只能靠猜");
            assertFalse(rule.path("number").asText().isBlank(),
                    () -> "规则 " + ruleId + " 没有 number，渲染出的编号会错位");
            assertFalse(rule.path("why").asText().isBlank(),
                    () -> "规则 " + ruleId + " 没有 why：以后没人知道这条为什么要这么写");
        }
    }

    @Test
    @DisplayName("清单自身合法：id 唯一，且语义层规则都绑定了 code")
    void rulesFileIsWellFormed() {
        Set<String> ids = new HashSet<>();
        for (JsonNode rule : rules) {
            String ruleId = rule.path("id").asText();
            assertTrue(ids.add(ruleId), () -> "规则 id 重复：" + ruleId);
            String enforcedBy = rule.path("enforced_by").asText();
            assertNotNull(enforcedBy);
            if ("validator".equals(enforcedBy)) {
                assertTrue(rule.path("codes").size() > 0,
                        () -> "规则 " + ruleId + " 声明由校验器执行，但没写 codes，本用例无法验证它真的被执行");
            }
        }
        assertTrue(ids.size() >= 10, "规则清单条目过少，疑似被误删");
    }
}
