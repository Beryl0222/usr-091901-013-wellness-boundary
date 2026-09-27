"""验证疗愈服务边界治理的领域不变量。

每条测试对应契约中的一条业务原则或一个监管场景。
"""

import unittest
from datetime import date

from rules import (
    DECISION_MANUAL_REVIEW,
    DECISION_PUBLISHABLE,
    DECISION_SUPPLEMENT,
    DECISION_VIOLATION,
    RuleBook,
    RuleVersion,
    default_rulebook,
)
from governance import (
    CASE_CLOSED,
    EVIDENCE_CONSUMER,
    EVIDENCE_DEFENSE,
    EVIDENCE_INSPECTION,
    GovernanceError,
    GovernanceSystem,
    PARTY_HOTEL,
    PARTY_PLATFORM,
    PARTY_PROVIDER,
    REFUND_APPROVED,
    REFUND_DONE,
    REFUND_PROCESSING,
    STATUS_PUBLISHED,
    STATUS_RESTRICTED,
    STATUS_SUPPLEMENT,
)


def make_system():
    """一个已具备合规主体、人员、服务与合同的最小环境。"""
    sys = GovernanceSystem(rulebook=default_rulebook())
    sub = sys.register_subject("山月疗愈工作室", "9111000MA001", "杭州",
                               contact_phones=("13800000001",))
    sys.register_practitioner("林音", "音声疗愈师", ["疗愈服务从业备案"], sub.id, "杭州")
    svc = sys.declare_service(
        sub.id, "森林音声放松", "能量课程", "杭州",
        steps=["呼吸引导", "颂钵聆听"],
        credentials=["疗愈服务备案"], price=399.0,
        claims=["颂钵声音放松体验，帮助安静下来"],
    )
    contract = sys.create_contract(svc.id, "预付服务协议", {
        "cancellation": "未消费可随时取消",
        "refund_policy": "未消费部分按原价全额退回",
    })
    return sys, sub, svc, contract


def confirm_all(sys, svc):
    sys.confirm_responsibility(svc.id, PARTY_PLATFORM, "平台-客服主管")
    sys.confirm_responsibility(svc.id, PARTY_HOTEL, "酒店-前厅经理")
    sys.confirm_responsibility(svc.id, PARTY_PROVIDER, "主体-法定代表人")


class RuleEngineTest(unittest.TestCase):
    def test_relaxation_claim_publishable(self):
        result = default_rulebook().review_claim("水晶引导放松，适合周末减压")
        self.assertEqual(result.decision, DECISION_PUBLISHABLE)
        self.assertEqual(result.hits, ())

    def test_efficacy_claim_without_evidence_needs_supplement(self):
        result = default_rulebook().review_claim("七天排毒疏通经络")
        self.assertEqual(result.decision, DECISION_SUPPLEMENT)
        # 补证后同一用语可发布
        result2 = default_rulebook().review_claim(
            "七天排毒疏通经络", evidence_documents=("检测报告.pdf",))
        self.assertEqual(result2.decision, DECISION_PUBLISHABLE)

    def test_disease_claim_is_blocked_and_requires_human(self):
        result = default_rulebook().review_claim("音声疗愈可以治疗抑郁症和失眠症")
        self.assertEqual(result.decision, DECISION_VIOLATION)
        self.assertIn(DECISION_MANUAL_REVIEW, [h.decision for h in result.hits])
        # 快照必须带规则版本
        self.assertTrue(all(h.rule_version for h in result.hits))

    def test_rules_vary_by_region_and_time(self):
        book = default_rulebook()
        book.add_version(RuleVersion(
            version="HZ-2026-7", effective_from="2026-07-01", effective_to=None,
            region="杭州",
            practitioner_requirements={"音声疗愈师": ("疗愈服务从业备案", "杭州场所备案")},
            service_requirements={}, prohibited_claims=(), prohibited_steps=(),
            efficacy_claims=(),
        ))
        # 7 月前杭州不要求场所备案
        self.assertEqual(book.check_practitioner(
            "音声疗愈师", ["疗愈服务从业备案"], "杭州", date(2026, 6, 30)), [])
        # 7 月后叠加，地区规则只能更严
        hits = book.check_practitioner(
            "音声疗愈师", ["疗愈服务从业备案"], "杭州", date(2026, 7, 2))
        self.assertEqual(len(hits), 1)
        self.assertIn("杭州场所备案", hits[0].reason)
        self.assertEqual(hits[0].rule_version, "HZ-2026-7")
        # 其他地区不受影响
        self.assertEqual(book.check_practitioner(
            "音声疗愈师", ["疗愈服务从业备案"], "成都", date(2026, 7, 2)), [])

    def test_expired_rule_does_not_apply(self):
        book = RuleBook([RuleVersion(
            version="OLD-1", effective_from="2020-01-01", effective_to="2025-12-31",
            region="全国", practitioner_requirements={"禅修导师": ("旧资质",)},
            service_requirements={}, prohibited_claims=(), prohibited_steps=(),
            efficacy_claims=(),
        )])
        self.assertEqual(book.check_practitioner(
            "禅修导师", [], "杭州", date(2026, 1, 1)), [])

    def test_medical_step_is_violation(self):
        hits = default_rulebook().check_service(
            "能量课程", ["疗愈服务备案"], "杭州", steps=["放松引导", "针刺穴位"])
        self.assertTrue(any(h.decision == DECISION_VIOLATION for h in hits))


class DeclarationStatusTest(unittest.TestCase):
    def test_clean_service_is_published(self):
        sys, sub, svc, _ = make_system()
        self.assertEqual(svc.status, STATUS_PUBLISHED)

    def test_missing_credential_means_supplement(self):
        sys = GovernanceSystem()
        sub = sys.register_subject("新店", "9111000MA002", "杭州")
        svc = sys.declare_service(sub.id, "山居旅修", "旅修", "杭州",
                                  credentials=["疗愈服务备案"])  # 缺安全预案
        self.assertEqual(svc.status, STATUS_SUPPLEMENT)
        self.assertTrue(any("旅修安全预案" in r["reason"] for r in svc.status_reasons))

    def test_disease_claim_restricts_and_queues_review(self):
        sys, sub, svc, _ = make_system()
        review = sys.add_claim(svc.id, "本课程治疗焦虑症")
        self.assertEqual(svc.status, STATUS_RESTRICTED)
        self.assertFalse(svc.on_sale)
        self.assertEqual(review.manual_status, "待审查")
        self.assertIn(review.id, sys.review_queue)

    def test_restricted_service_cannot_go_on_sale(self):
        sys, sub, svc, _ = make_system()
        sys.add_claim(svc.id, "包治抑郁症")
        with self.assertRaises(GovernanceError):
            sys.set_on_sale(svc.id, True)

    def test_human_approval_unblocks_but_keeps_snapshot(self):
        sys, sub, svc, _ = make_system()
        review = sys.add_claim(svc.id, "对抑郁症有治疗效果")
        self.assertEqual(svc.status, STATUS_RESTRICTED)
        sys.take_review("审核员甲", review.id)
        sys.resolve_review(review.id, True, "材料证明仅为体验描述，已删词", "审核员甲")
        self.assertEqual(svc.status, STATUS_PUBLISHED)
        # 快照与人工结论都保留
        explained = sys.explain_claim(review.id)
        self.assertEqual(explained["manual_review"]["status"], "审查通过")
        self.assertTrue(explained["hits"])

    def test_human_rejection_keeps_restriction(self):
        sys, sub, svc, _ = make_system()
        review = sys.add_claim(svc.id, "治疗失眠症")
        sys.take_review("审核员乙", review.id)
        sys.resolve_review(review.id, False, "无法提供任何医学依据", "审核员乙")
        self.assertEqual(svc.status, STATUS_RESTRICTED)


class ResponsibilityTest(unittest.TestCase):
    def test_three_parties_confirm_separately(self):
        sys, sub, svc, _ = make_system()
        status = sys.responsibility_status(svc.id)
        self.assertFalse(status["all_confirmed"])
        sys.confirm_responsibility(svc.id, PARTY_PLATFORM, "平台代表")
        status = sys.responsibility_status(svc.id)
        self.assertTrue(status[PARTY_PLATFORM]["confirmed"])
        self.assertFalse(status[PARTY_HOTEL]["confirmed"])
        self.assertFalse(status[PARTY_PROVIDER]["confirmed"])
        sys.confirm_responsibility(svc.id, PARTY_HOTEL, "酒店代表")
        sys.confirm_responsibility(svc.id, PARTY_PROVIDER, "提供者代表")
        self.assertTrue(sys.responsibility_status(svc.id)["all_confirmed"])

    def test_package_sale_requires_all_confirmations(self):
        sys, sub, svc, contract = make_system()
        with self.assertRaises(GovernanceError):
            sys.create_order(svc.id, "王某", [{"name": "十次卡", "total": 10, "price": 300}],
                             contract.id)
        confirm_all(sys, svc)
        order = sys.create_order(svc.id, "王某",
                                 [{"name": "十次卡", "total": 10, "price": 300}], contract.id)
        self.assertEqual(order.total_amount, 3000)


class ContractAndRefundTest(unittest.TestCase):
    def setUp(self):
        self.sys, self.sub, self.svc, self.contract = make_system()
        confirm_all(self.sys, self.svc)
        self.order = self.sys.create_order(
            self.svc.id, "陈某",
            [{"name": "十次音声卡", "total": 10, "price": 300}], self.contract.id)

    def test_amendment_keeps_original_version(self):
        self.assertEqual(self.order.events[0]["contract_version"], 1)
        self.sys.amend_contract(self.contract.id, {
            "cancellation": "提前7天", "refund_policy": "收20%手续费"})
        self.assertEqual(self.contract.current_version, 2)
        v1 = next(v for v in self.contract.versions if v.version == 1)
        self.assertEqual(v1.terms["refund_policy"], "未消费部分按原价全额退回")
        self.assertEqual(v1.superseded_by, 2)

    def test_refund_uses_original_contract_after_amendment(self):
        # 消费 3 次后修订合同，退款仍按原合同与原价
        self.sys.consume(self.order.id, "十次音声卡", 3)
        self.sys.amend_contract(self.contract.id, {
            "cancellation": "提前7天", "refund_policy": "收20%手续费"})
        refund = self.sys.unconsumed_refund(self.order.id, "酒店客服", "客户行程变更")
        self.assertEqual(refund["amount"], 7 * 300)
        self.assertEqual(refund["contract_version"], 1)
        self.assertEqual(refund["contract_terms"], "未消费部分按原价全额退回")
        for stage in (REFUND_APPROVED, REFUND_PROCESSING, REFUND_DONE):
            self.sys.advance_refund(self.order.id, refund["id"], stage, "财务")
        tracking = self.sys.refund_tracking(self.order.id)
        self.assertEqual(tracking["refunds"][0]["status"], REFUND_DONE)
        stages = [h["stage"] for h in tracking["refunds"][0]["stage_history"]]
        self.assertEqual(stages, ["已申请", "已核定", "执行中", "已退款"])
        self.assertEqual(self.order.status, "已退清")

    def test_split_and_transfer_keep_original_contract(self):
        child = self.sys.split_package(self.order.id, "十次音声卡", 3, "李某", "陈某")
        self.assertEqual(child.contract_id, self.order.contract_id)
        self.assertEqual(child.total_amount, 900)
        self.assertEqual(child.events[0]["contract_version"], 1)
        record = self.sys.transfer_package(self.order.id, "十次音声卡", 2, "周某", "陈某")
        self.assertEqual(record["contract_version"], 1)
        # 父单剩余 7 次
        src = self.order.items[0]
        self.assertEqual(src["total"], 7)

    def test_cancel_then_no_double_cancel(self):
        self.sys.cancel_order(self.order.id, "陈某", "不想去了")
        with self.assertRaises(GovernanceError):
            self.sys.cancel_order(self.order.id, "陈某", "再取消一次")

    def test_no_refund_when_fully_consumed(self):
        self.sys.consume(self.order.id, "十次音声卡", 10)
        with self.assertRaises(GovernanceError):
            self.sys.unconsumed_refund(self.order.id, "陈某")


class CaseFlowTest(unittest.TestCase):
    def setUp(self):
        self.sys, self.sub, self.svc, self.contract = make_system()
        confirm_all(self.sys, self.svc)
        self.order = self.sys.create_order(
            self.svc.id, "赵某", [{"name": "次卡", "total": 5, "price": 300}], self.contract.id)

    def test_evidence_buckets_are_isolated(self):
        c1 = self.sys.file_complaint("宣传说能治头疼", "赵某", self.sub.id, self.svc.id)
        case = self.sys.open_case(self.sub.id, self.svc.id, order_id=self.order.id,
                                  complaint_ids=[c1.id])
        self.sys.add_evidence(case.id, EVIDENCE_INSPECTION, "巡查员钱", "现场发现医疗用语海报")
        self.sys.add_evidence(case.id, EVIDENCE_CONSUMER, "赵某", "聊天记录截图", ["img1.png"])
        self.sys.add_evidence(case.id, EVIDENCE_DEFENSE, "工作室", "该文案系外包误发")
        # 经营者看不到巡查与消费者证据
        merchant = self.sys.view_evidence(case.id, "经营者")
        self.assertEqual({e["bucket"] for e in merchant}, {EVIDENCE_DEFENSE})
        consumer = self.sys.view_evidence(case.id, "消费者")
        self.assertEqual({e["bucket"] for e in consumer}, {EVIDENCE_CONSUMER})
        # 审核员可见全部
        officer = self.sys.view_evidence(case.id, "案件审核员")
        self.assertEqual(len(officer), 3)

    def test_repeated_complaints_link_but_never_auto_convict(self):
        ids = [self.sys.file_complaint("夸大功效", f"用户{i}", self.sub.id, self.svc.id).id
               for i in range(3)]
        case = self.sys.open_case(self.sub.id, self.svc.id)
        for cid in ids:
            self.sys.link_complaint(case.id, cid)
        self.assertEqual(len(case.complaint_ids), 3)
        # 无论多少投诉，系统层面都没有违法认定
        self.assertIsNone(case.ruling)
        self.assertIsNone(case.ruling_officer)
        # 只有审核员能作出认定
        self.sys.issue_ruling(case.id, "构成虚假宣传，责令改正", "审核员甲")
        self.assertEqual(case.ruling_officer, "审核员甲")

    def test_restriction_action_reports_impact(self):
        # 同主体另一在售服务
        other = self.sys.declare_service(
            self.sub.id, "溪边水晶放松", "能量课程", "杭州",
            credentials=["疗愈服务备案"], claims=["放松身心"])
        self.sys.set_on_sale(other.id, True)
        self.sys.set_on_sale(self.svc.id, True)
        review = self.sys.add_claim(self.svc.id, "治疗高血压")
        case = self.sys.open_case(self.sub.id, self.svc.id, review.id)
        result = self.sys.restrict_service(self.svc.id, case.id, "疾病治疗宣传", "审核员甲")
        self.assertFalse(self.svc.on_sale)
        impacted = [a["service_id"] for a in result["affected"]["other_on_sale_services"]]
        self.assertIn(other.id, impacted)
        self.assertEqual(self.svc.status, STATUS_RESTRICTED)

    def test_close_case_records_handling(self):
        case = self.sys.open_case(self.sub.id, self.svc.id)
        self.sys.close_case(case.id, "审核员甲", "责令删除违规宣传")
        self.assertEqual(case.status, CASE_CLOSED)
        self.assertEqual(case.handling_actions[0]["action"], "责令删除违规宣传")


class AliasDetectionTest(unittest.TestCase):
    def test_detect_renamed_cross_region_subject(self):
        sys = GovernanceSystem()
        a = sys.register_subject("山月健康管理有限公司", "91330100MA9", "杭州",
                                 contact_phones=("13900001111",))
        # 换名在成都继续经营：同电话 + 近似名称（无信用代码）
        b = sys.register_subject("成都山月健康工作室", "", "成都",
                                 contact_phones=("13900001111",))
        findings = sys.find_alias_subjects(a.id)
        self.assertEqual(len(findings["matches"]), 1)
        match = findings["matches"][0]
        self.assertEqual(match["subject_id"], b.id)
        self.assertTrue(any("联系电话" in c for c in match["clues"]))
        self.assertTrue(any("名称近似" in c for c in match["clues"]))

    def test_same_credit_code_merges_alias(self):
        sys = GovernanceSystem()
        a = sys.register_subject("旧名", "91330100MA8", "杭州")
        b = sys.register_subject("新名疗愈", "91330100MA8", "海南")
        self.assertEqual(a.id, b.id)  # 同信用代码合并为同一主体
        self.assertIn("新名疗愈", a.aliases)

    def test_unrelated_subjects_not_flagged(self):
        sys = GovernanceSystem()
        a = sys.register_subject("阿尔法工作室", "", "杭州", contact_phones=("13700000000",))
        sys.register_subject("贝塔酒店管理有限公司", "", "成都", contact_phones=("13800000000",))
        self.assertEqual(sys.find_alias_subjects(a.id)["matches"], [])


class ExplainabilityTest(unittest.TestCase):
    def test_explain_claim_reconstructs_rules_in_force_then(self):
        sys, sub, svc, _ = make_system()
        review = sys.add_claim(svc.id, "宣称根治糖尿病")
        explanation = sys.explain_claim(review.id)
        self.assertEqual(explanation["decision"], DECISION_VIOLATION)
        self.assertTrue(explanation["rule_versions_in_force"])
        self.assertEqual(explanation["rule_versions_in_force"][0]["version"], "NR-2026-1")
        self.assertTrue(any("糖尿病" in h["reason"] for h in explanation["hits"]))

    def test_refund_tracking_shows_full_paper_trail(self):
        sys, sub, svc, contract = make_system()
        confirm_all(sys, svc)
        order = sys.create_order(svc.id, "钱某",
                                 [{"name": "季卡", "total": 3, "price": 1000}], contract.id)
        refund = sys.unconsumed_refund(order.id, "钱某")
        sys.advance_refund(order.id, refund["id"], REFUND_APPROVED, "平台财务")
        tracking = sys.refund_tracking(order.id)
        self.assertEqual(tracking["original_contract_version"], 1)
        self.assertEqual(tracking["refunds"][0]["status"], REFUND_APPROVED)
        types = [e["type"] for e in tracking["events"]]
        self.assertIn("退款申请", types)


if __name__ == "__main__":
    unittest.main()
