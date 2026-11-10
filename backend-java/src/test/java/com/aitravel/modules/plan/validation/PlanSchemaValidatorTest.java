package com.aitravel.modules.plan.validation;

import com.aitravel.support.PlanMutants;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 契约校验回归（AC-07 的第一层证据）。
 * <p>
 * 拿一份合规样例做基准，由 {@link PlanMutants} 施加 50 组「只改一处」的变异，
 * 每组都必须被拒绝——包括 Schema 层和 S-1~S-6 语义层。
 * 这个用例的价值不在「跑通」，而在<b>任何一条漏网都会指名道姓地报出来</b>。
 */
class PlanSchemaValidatorTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private static PlanSchemaValidator validator;
    private static JsonNode validDraft;

    @BeforeAll
    static void setUp() {
        validator = new PlanSchemaValidator(MAPPER);
        validDraft = PlanMutants.validDraft();
    }

    @Test
    @DisplayName("合规样例必须通过（否则后面 50 组变异没有意义）")
    void validSamplePasses() {
        List<PlanViolation> violations = validator.validate(validDraft);
        assertTrue(violations.isEmpty(), () -> "合规样例被误拒：" + violations);
        assertEquals(validDraft.path("unverifiedFieldCount").asInt(),
                validator.countUnverifiedFactFields(validDraft),
                "样例声明的 unverifiedFieldCount 与实际 FactField 数量必须一致");
    }

    @Test
    @DisplayName("50 组畸形样例必须全部被拒绝")
    void allFiftyMalformedDraftsAreRejected() {
        List<PlanMutants.Mutant> mutants = PlanMutants.all();
        assertEquals(50, mutants.size(), "畸形样例数量必须是 50（验收口径）");

        List<String> escaped = new ArrayList<>();
        for (PlanMutants.Mutant mutant : mutants) {
            ObjectNode node = validDraft.deepCopy();
            mutant.mutator().accept(node);
            if (validator.validate(node).isEmpty()) {
                escaped.add(mutant.name());
            }
        }
        assertTrue(escaped.isEmpty(),
                () -> "以下畸形样例未被拒绝，说明契约或校验器存在漏洞：" + escaped);
    }

    @Test
    @DisplayName("S-5 计数不得采信自报值")
    void declaredCountIsNotTrusted() {
        ObjectNode node = validDraft.deepCopy();
        node.put("unverifiedFieldCount", 0);
        List<PlanViolation> violations = validator.validate(node);
        assertFalse(violations.isEmpty());
        assertTrue(violations.stream().anyMatch(v -> "S-5".equals(v.code())),
                () -> "应命中 S-5，实际：" + violations);
    }

    @Test
    @DisplayName("状态机：未确认不得开始，终态不可再迁移")
    void stateMachineRejectsIllegalTransitions() {
        assertTrue(PlanStatus.DRAFT.canMoveTo(PlanStatus.CONFIRMED));
        assertFalse(PlanStatus.DRAFT.canMoveTo(PlanStatus.IN_PROGRESS), "草稿不得直接开始（AC-09）");
        assertFalse(PlanStatus.DRAFT.canMoveTo(PlanStatus.COMPLETED));
        assertFalse(PlanStatus.COMPLETED.canMoveTo(PlanStatus.CANCELLED), "已完成不得再取消");
        assertFalse(PlanStatus.CANCELLED.canMoveTo(PlanStatus.CONFIRMED));
        assertTrue(PlanStatus.CONFIRMED.canMoveTo(PlanStatus.IN_PROGRESS));
        assertTrue(PlanStatus.IN_PROGRESS.canMoveTo(PlanStatus.COMPLETED));
    }
}
