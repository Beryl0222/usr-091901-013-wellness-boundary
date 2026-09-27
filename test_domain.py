"""领域流程测试：申报评估、责任确认、合同版本、案件隔离与监管解释。"""

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import service
from wellness import cases, contracts, declaration, evaluation, oversight, responsibility, rules
from wellness.store import Store
from wellness.util import DomainError, Forbidden

REGION = "浙江/杭州"


def new_subject(store, **overrides):
    data = {"name": "静心疗愈社", "credit_code": "91330100MAAAAA001X",
            "legal_person": "王某", "legal_person_id": "LP-001", "region": REGION,
            "contact_phone": "13800000000", "address": "西湖区某路 1 号"}
    data.update(overrides)
    return declaration.declare_subject(store, **data)


def new_service(store, subject_id, **overrides):
    data = {"subject_id": subject_id, "name": "颂钵音声体验", "category": "音声疗愈",
            "region": REGION, "steps": ["迎宾引导", "颂钵音声体验", "茶歇交流"],
            "price": {"amount": 399, "unit": "次", "prepaid": True},
            "claims": ["舒缓放松", "改善睡眠氛围"], "channels": ["渠道平台", "酒店"]}
    data.update(overrides)
    return declaration.declare_service(store, **data)


class EvaluationTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.subject = new_subject(self.store)

    def test_missing_credential_then_supplemented(self):
        rules.add_admission_rule(self.store, rule_key="音声准入", region="浙江",
                                 categories=["音声疗愈"], required_credentials=["音声疗愈师"],
                                 effective_from="2026-01-01")
        svc = new_service(self.store, self.subject["id"])
        first = evaluation.evaluate_service(self.store, svc["id"], at="2026-06-01T00:00:00+00:00")
        self.assertEqual(first["status"], "补充材料")
        self.assertIn("音声疗愈师", first["hits"][0]["detail"])
        declaration.declare_practitioner(self.store, subject_id=self.subject["id"], name="李某",
                                         id_number="P-1",
                                         credentials=[{"type": "音声疗愈师", "number": "ZS-1",
                                                       "issuer": "行业协会", "valid_until": "2027-01-01"}])
        second = evaluation.evaluate_service(self.store, svc["id"], at="2026-06-02T00:00:00+00:00")
        self.assertEqual(second["status"], "允许发布")

    def test_disease_claim_requires_manual_review(self):
        svc = new_service(self.store, self.subject["id"], claims=["颂钵可以治愈失眠"])
        result = evaluation.evaluate_service(self.store, svc["id"])
        self.assertEqual(result["status"], "待人工审查")
        claim = self.store.find("claims", service_id=svc["id"])[0]
        self.assertEqual(claim["status"], "待人工审查")
        evaluation.review_claim(self.store, claim["id"], reviewer="审核员甲",
                                decision="限制", note="涉及疾病治疗主张")
        self.assertEqual(self.store.get("claims", claim["id"])["status"], "被限制")
        self.assertEqual(self.store.get("services", svc["id"])["status"], "越界")

    def test_manual_approval_lets_service_publish(self):
        svc = new_service(self.store, self.subject["id"], claims=["正念练习辅助治疗焦虑"])
        evaluation.evaluate_service(self.store, svc["id"])
        claim = self.store.find("claims", service_id=svc["id"])[0]
        evaluation.review_claim(self.store, claim["id"], reviewer="审核员甲",
                                decision="通过", note="已确认仅为体验型表述")
        self.assertEqual(self.store.get("services", svc["id"])["status"], "允许发布")

    def test_negative_entry_marks_violation(self):
        rules.add_negative_entry(self.store, rule_key="宣传禁令", region="*", kind="宣传用语",
                                 pattern="包治百病", severity="越界", effective_from="2026-01-01")
        svc = new_service(self.store, self.subject["id"], claims=["水晶能量包治百病"])
        result = evaluation.evaluate_service(self.store, svc["id"])
        self.assertEqual(result["status"], "越界")
        claim = self.store.find("claims", service_id=svc["id"])[0]
        self.assertEqual(claim["status"], "被限制")

    def test_prepaid_cap_from_negative_list(self):
        rules.add_negative_entry(self.store, rule_key="预付费上限", region="浙江", kind="预付捆绑",
                                 pattern="3000", severity="人工", effective_from="2026-01-01")
        svc = new_service(self.store, self.subject["id"],
                          price={"amount": 5000, "unit": "套餐", "prepaid": True})
        result = evaluation.evaluate_service(self.store, svc["id"])
        self.assertEqual(result["status"], "待人工审查")
        self.assertIn("预付金额超过上限", result["hits"][0]["detail"])

    def test_rules_follow_region_and_time(self):
        rules.add_admission_rule(self.store, rule_key="旅修准入", region="浙江",
                                 categories=["禅修旅修"], required_credentials=["旅修安全员"],
                                 effective_from="2026-06-01", effective_to="2026-12-31")
        svc = new_service(self.store, self.subject["id"], category="禅修旅修")
        before = evaluation.evaluate_service(self.store, svc["id"], at="2026-05-01T00:00:00+00:00")
        self.assertEqual(before["status"], "允许发布")
        during = evaluation.evaluate_service(self.store, svc["id"], at="2026-07-01T00:00:00+00:00")
        self.assertEqual(during["status"], "补充材料")
        other_region = new_service(self.store, self.subject["id"], category="禅修旅修", region="江苏/南京")
        elsewhere = evaluation.evaluate_service(self.store, other_region["id"],
                                                at="2026-07-01T00:00:00+00:00")
        self.assertEqual(elsewhere["status"], "允许发布")

    def test_explain_claim_at_that_time(self):
        rules.add_negative_entry(self.store, rule_key="宣传禁令", region="*", kind="宣传用语",
                                 pattern="代替就医", severity="越界", effective_from="2026-06-01",
                                 note="不得暗示替代医疗")
        svc = new_service(self.store, self.subject["id"], claims=["能量疗愈可代替就医"])
        evaluation.evaluate_service(self.store, svc["id"], at="2026-05-01T00:00:00+00:00")
        evaluation.evaluate_service(self.store, svc["id"], at="2026-07-01T00:00:00+00:00")
        claim = self.store.find("claims", service_id=svc["id"])[0]
        early = oversight.explain_claim(self.store, claim["id"], at="2026-05-15T00:00:00+00:00")
        self.assertFalse(early["restricted"])
        later = oversight.explain_claim(self.store, claim["id"], at="2026-07-15T00:00:00+00:00")
        self.assertTrue(later["restricted"])
        self.assertEqual(later["hits"][0]["source"], "负面清单")
        self.assertEqual(later["hits"][0]["version"], 1)
        self.assertEqual(later["rule_snapshot"][0]["note"], "不得暗示替代医疗")

    def test_parse_time_tolerates_space_decoded_timezone(self):
        # 查询串中的 + 会被解码为空格，parse_time 需要兼容
        from wellness.util import parse_time
        self.assertEqual(parse_time("2026-05-15T00:00:00 00:00"),
                         parse_time("2026-05-15T00:00:00+00:00"))
        self.assertEqual(parse_time("2026-05-15").isoformat(), "2026-05-15T00:00:00+00:00")


class ResponsibilityTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.subject = new_subject(self.store)
        self.service = new_service(self.store, self.subject["id"])
        evaluation.evaluate_service(self.store, self.service["id"])

    def test_on_sale_only_after_all_parties_confirm(self):
        self.assertEqual(responsibility.on_sale_services(self.store), [])
        responsibility.confirm_party(self.store, self.service["id"], party="渠道平台",
                                     confirmer="平台运营", terms_version="T-2026")
        responsibility.confirm_party(self.store, self.service["id"], party="酒店",
                                     confirmer="酒店经理", terms_version="T-2026")
        self.assertEqual(responsibility.on_sale_services(self.store), [])
        responsibility.confirm_party(self.store, self.service["id"], party="实际提供者",
                                     confirmer="静心疗愈社", terms_version="T-2026")
        self.assertEqual([s["id"] for s in responsibility.on_sale_services(self.store)],
                         [self.service["id"]])

    def test_terms_version_mismatch_rejected(self):
        responsibility.confirm_party(self.store, self.service["id"], party="酒店",
                                     confirmer="经理", terms_version="T-2026")
        with self.assertRaises(DomainError):
            responsibility.confirm_party(self.store, self.service["id"], party="渠道平台",
                                         confirmer="运营", terms_version="T-2025")


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        subject = new_subject(self.store)
        self.service = new_service(self.store, subject["id"])
        self.contract = contracts.create_contract(self.store, consumer="陈女士",
                                                  service_id=self.service["id"], units=10,
                                                  unit_price=100, terms="预付十次卡")

    def test_versions_kept_across_split_transfer_cancel_refund(self):
        contracts.record_consumption(self.store, self.contract["id"], 4)
        children = contracts.split_contract(self.store, self.contract["id"], [4, 2])
        parent = self.store.get("contracts", self.contract["id"])
        self.assertEqual(parent["status"], "已拆分")
        self.assertEqual(parent["versions"][0]["change"], "订立")
        self.assertEqual(parent["versions"][0]["snapshot"]["consumer"], "陈女士")
        self.assertEqual(children[0]["origin"], {"contract_id": parent["id"], "version": 2})
        child = children[1]
        contracts.transfer_contract(self.store, child["id"], "林女士")
        moved = self.store.get("contracts", child["id"])
        self.assertEqual(moved["consumer"], "林女士")
        self.assertEqual(moved["versions"][0]["snapshot"]["consumer"], "陈女士")
        result = contracts.cancel_contract(self.store, child["id"], reason="行程变化")
        refund_order = result["refund"]
        self.assertEqual(refund_order["amount"], 200)
        self.assertEqual(refund_order["contract_version"], 3)
        for _ in range(3):
            contracts.advance_refund(self.store, refund_order["id"])
        progress = oversight.refund_progress(self.store, refund_order["id"])
        self.assertTrue(progress["completed"])
        self.assertEqual(progress["current_step"], "到账")
        self.assertEqual([s["step"] for s in progress["timeline"]], ["申请", "审核", "执行", "到账"])
        self.assertEqual(self.store.get("contracts", child["id"])["status"], "已退完")

    def test_split_must_cover_remaining_units(self):
        contracts.record_consumption(self.store, self.contract["id"], 4)
        with self.assertRaises(DomainError):
            contracts.split_contract(self.store, self.contract["id"], [1, 1])

    def test_duplicate_refund_rejected_and_reject_restores(self):
        refund_order = contracts.request_refund(self.store, self.contract["id"], reason="未消费")
        with self.assertRaises(DomainError):
            contracts.request_refund(self.store, self.contract["id"], reason="重复申请")
        contracts.advance_refund(self.store, refund_order["id"], reject=True, note="材料不全")
        self.assertEqual(self.store.get("contracts", self.contract["id"])["status"], "有效")
        self.assertEqual(refund_order["status"], "已驳回")


class CaseTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.subject = new_subject(self.store)
        self.service = new_service(self.store, self.subject["id"])
        evaluation.evaluate_service(self.store, self.service["id"])
        for party in responsibility.PARTIES:
            responsibility.confirm_party(self.store, self.service["id"], party=party,
                                         confirmer=f"{party}经办", terms_version="T-2026")

    def test_compartments_isolated_by_role(self):
        case = cases.open_case(self.store, source="投诉", subject_id=self.subject["id"],
                               service_id=self.service["id"], opened_by="巡查员甲")
        cases.add_material(self.store, case["id"], role="消费者", author="陈女士",
                           title="付款记录", content="转账截图")
        cases.add_material(self.store, case["id"], role="巡查人员", author="巡查员甲",
                           title="现场检查", content="发现夸大宣传")
        cases.add_material(self.store, case["id"], role="经营者", author="静心疗愈社",
                           title="申辩", content="宣传为体验描述")
        with self.assertRaises(Forbidden):
            cases.add_material(self.store, case["id"], role="案件审核员", author="审核员",
                               title="越权", content="审核员不提交材料")
        merchant_view = cases.read_case(self.store, case["id"], role="经营者")
        self.assertEqual(list(merchant_view["compartments"].keys()), ["商家申辩"])
        reviewer_view = cases.read_case(self.store, case["id"], role="案件审核员")
        self.assertEqual(set(reviewer_view["compartments"].keys()), set(cases.COMPARTMENTS))
        with self.assertRaises(Forbidden):
            cases.read_case(self.store, case["id"], role="路人")

    def test_link_does_not_change_status(self):
        first = cases.open_case(self.store, source="投诉", subject_id=self.subject["id"],
                                opened_by="消费者A")
        second = cases.open_case(self.store, source="投诉", subject_id=self.subject["id"],
                                 opened_by="消费者B")
        cases.link_cases(self.store, first["id"], second["id"], reason="同一主体重复投诉")
        self.assertEqual(self.store.get("cases", first["id"])["status"], "投诉处理中")
        self.assertEqual(self.store.get("cases", second["id"])["status"], "投诉处理中")
        self.assertEqual(self.store.get("cases", first["id"])["links"][0]["case_id"], second["id"])
        self.assertEqual(self.store.get("cases", second["id"])["links"][0]["case_id"], first["id"])

    def test_close_limits_on_sale_services(self):
        self.assertEqual(len(responsibility.on_sale_services(self.store)), 1)
        case = cases.open_case(self.store, source="巡查", subject_id=self.subject["id"],
                               opened_by="巡查员甲")
        disposition = cases.close_case(self.store, case["id"], reviewer="审核员乙",
                                       decision="夸大宣传，责令限期整改",
                                       rule_refs=[{"rule_id": "neg-0001", "version": 1}])
        self.assertEqual(disposition["affected_service_ids"], [self.service["id"]])
        self.assertEqual(self.store.get("services", self.service["id"])["status"], "限制经营")
        impact = oversight.disposition_impact(self.store, disposition["id"])
        self.assertEqual(impact["affected_services"][0]["status"], "限制经营")
        self.assertEqual(impact["case_status"], "已结案")
        with self.assertRaises(DomainError):
            cases.add_material(self.store, case["id"], role="消费者", author="陈女士",
                               title="补充", content="结案后不能再提交")


class RenamedSubjectTest(unittest.TestCase):
    def test_cross_region_rename_detected(self):
        store = Store()
        new_subject(store)
        new_subject(store, name="安澜健康咨询", region="江苏/南京", contact_phone="13911111111")
        leads = oversight.renamed_subject_leads(store)
        self.assertEqual(len(leads), 1)
        lead = leads[0]
        self.assertTrue(lead["cross_region"])
        self.assertIn("统一社会信用代码相同", lead["signals"])

    def test_same_name_or_unrelated_not_flagged(self):
        store = Store()
        new_subject(store)
        new_subject(store, region="江苏/南京")
        new_subject(store, name="其他主体", credit_code="91330100BBBBB002X",
                    legal_person_id="LP-002", contact_phone="13700000000", region="江苏/南京")
        self.assertEqual(oversight.renamed_subject_leads(store), [])


class HttpApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = service.create_server(0, Store(), host="127.0.0.1")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def post(self, path, payload):
        request = Request(self.base + path, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=2) as response:
            return json.load(response)

    def get(self, path):
        with urlopen(self.base + path, timeout=2) as response:
            return json.load(response)

    def test_full_flow_over_http(self):
        subject = self.post("/subjects", {"name": "静心疗愈社", "credit_code": "91330100MAAAAA001X",
                                          "legal_person": "王某", "legal_person_id": "LP-001",
                                          "region": REGION, "contact_phone": "13800000000",
                                          "address": "西湖区某路 1 号"})
        svc = self.post("/services", {"subject_id": subject["id"], "name": "颂钵音声体验",
                                      "category": "音声疗愈", "region": REGION,
                                      "steps": ["迎宾引导", "颂钵音声体验"],
                                      "price": {"amount": 399, "unit": "次", "prepaid": False},
                                      "claims": ["舒缓放松"]})
        result = self.post(f"/services/{svc['id']}/evaluate", {})
        self.assertEqual(result["status"], "允许发布")
        for party in ("渠道平台", "酒店", "实际提供者"):
            self.post(f"/responsibility/{svc['id']}/confirm",
                      {"party": party, "confirmer": "经办", "terms_version": "T-2026"})
        detail = self.get(f"/services/{svc['id']}")
        self.assertTrue(detail["on_sale"])

    def test_errors_map_to_status_codes(self):
        with self.assertRaises(HTTPError) as not_found:
            self.get("/nope")
        self.assertEqual(not_found.exception.code, 404)
        not_found.exception.close()
        with self.assertRaises(HTTPError) as missing_subject:
            self.post("/cases", {"source": "投诉", "subject_id": "sub-9999", "opened_by": "x"})
        self.assertEqual(missing_subject.exception.code, 404)
        missing_subject.exception.close()
        with self.assertRaises(HTTPError) as bad_request:
            self.post("/subjects", {"name": "", "credit_code": ""})
        self.assertEqual(bad_request.exception.code, 400)
        bad_request.exception.close()


if __name__ == "__main__":
    unittest.main()
