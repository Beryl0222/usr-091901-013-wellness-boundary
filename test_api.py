"""HTTP 接口端到端测试：角色约束、证据隔离与全链路流程。"""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from service import Handler


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=2)

    def call(self, method, path, payload=None, role=None, actor=None):
        payload = dict(payload or {})
        if role:
            payload["role"] = role
        if actor:
            payload["actor"] = actor
        if method == "GET":
            from urllib.parse import urlencode
            qs = urlencode({k: v for k, v in payload.items() if v is not None})
            path = f"{path}?{qs}" if qs else path
            data = None
        else:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(f"{self.base}{path}", data=data,
                      headers={"Content-Type": "application/json"}, method=method)
        try:
            with urlopen(req, timeout=3) as resp:
                return resp.status, json.load(resp)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def setUp(self):
        # 每个用例用全新后端，避免类级共享状态
        Handler.api = type(Handler.api)()
        self.status, self.subject = self.call("POST", "/subjects", {
            "name": "山月工作室", "unified_code": "9111000MA777", "region": "杭州"})
        self.call("POST", "/practitioners", {
            "name": "林音", "practitioner_type": "音声疗愈师",
            "credentials": ["疗愈服务从业备案"],
            "subject_id": self.subject["id"], "region": "杭州"})
        _, self.service = self.call("POST", "/services", {
            "subject_id": self.subject["id"], "name": "森林音声放松",
            "service_type": "能量课程", "region": "杭州",
            "steps": ["呼吸引导"], "credentials": ["疗愈服务备案"],
            "price": 399, "claims": ["帮助放松安静下来"]})
        _, self.contract = self.call("POST", "/contracts", {
            "service_id": self.service["id"], "title": "预付协议",
            "terms": {"refund_policy": "未消费全退"}})

    # ------------------------------------------------------------ 基础
    def test_health_and_contract(self):
        with urlopen(f"{self.base}/health", timeout=2) as r:
            self.assertEqual(json.load(r)["status"], "ok")

    # ----------------------------------------------------- 准入与宣传
    def test_disease_claim_restricts_and_manual_flow(self):
        _, review = self.call("POST", f"/services/{self.service['id']}/claims",
                              {"text": "可以治疗抑郁症"})
        self.assertEqual(review["result"]["decision"], "越界")
        _, svc = self.call("GET", f"/services/{self.service['id']}")
        self.assertEqual(svc["status"], "限制经营")
        # 非审核员不能领取
        code, _ = self.call("POST", "/reviews/take",
                            {"claim_id": review["id"]}, role="经营者", actor="商家")
        self.assertEqual(code, 403)
        # 审核员领取并维持限制
        self.call("POST", "/reviews/take",
                  {"claim_id": review["id"]}, role="案件审核员", actor="审核员甲")
        _, resolved = self.call("POST", f"/reviews/{review['id']}/resolve",
                                {"approve": False, "note": "无依据"},
                                role="案件审核员", actor="审核员甲")
        self.assertEqual(resolved["manual_status"], "维持限制")

    # ------------------------------------------------------- 三方责任
    def test_three_party_responsibility_roles(self):
        sid = self.service["id"]
        code, _ = self.call("POST", f"/services/{sid}/responsibility",
                            {"party": "线上平台"}, role="酒店", actor="酒店前台")
        self.assertEqual(code, 403)  # 酒店不能代平台确认
        self.call("POST", f"/services/{sid}/responsibility",
                  {"party": "线上平台"}, role="渠道平台", actor="平台经理")
        self.call("POST", f"/services/{sid}/responsibility",
                  {"party": "酒店"}, role="酒店", actor="酒店经理")
        code, body = self.call("POST", f"/services/{sid}/responsibility",
                               {"party": "实际提供者"}, role="经营者", actor="法人")
        self.assertEqual(code, 200)
        self.assertTrue(body["all_confirmed"])
        # 未确认前不能下单——这里已确认，下单应成功
        _, order = self.call("POST", "/orders", {
            "service_id": sid, "buyer": "王某",
            "items": [{"name": "十次卡", "total": 10, "price": 300}],
            "contract_id": self.contract["id"]})
        self.assertEqual(order["total_amount"], 3000.0)

    def test_order_blocked_without_confirmation(self):
        _, svc2 = self.call("POST", "/services", {
            "subject_id": self.subject["id"], "name": "水晶放松",
            "service_type": "能量课程", "region": "杭州",
            "credentials": ["疗愈服务备案"], "price": 199})
        _, contract2 = self.call("POST", "/contracts", {
            "service_id": svc2["id"], "title": "协议2", "terms": {}})
        code, err = self.call("POST", "/orders", {
            "service_id": svc2["id"], "buyer": "李某",
            "items": [{"name": "单次", "total": 1, "price": 199}],
            "contract_id": contract2["id"]})
        self.assertEqual(code, 400)
        self.assertIn("责任", err["error"])

    # ----------------------------------------------------- 退款全链路
    def test_refund_tracking_over_http(self):
        sid = self.service["id"]
        for party, role in [("线上平台", "渠道平台"), ("酒店", "酒店"),
                            ("实际提供者", "经营者")]:
            self.call("POST", f"/services/{sid}/responsibility",
                      {"party": party}, role=role, actor=role)
        _, order = self.call("POST", "/orders", {
            "service_id": sid, "buyer": "陈某",
            "items": [{"name": "十次卡", "total": 10, "price": 300}],
            "contract_id": self.contract["id"]})
        self.call("POST", f"/orders/{order['id']}/consume",
                  {"item": "十次卡", "quantity": 3}, actor="陈某")
        _, refund = self.call("POST", f"/orders/{order['id']}/refund",
                              {"note": "行程变更"}, actor="陈某")
        self.assertEqual(refund["amount"], 2100)
        self.assertEqual(refund["contract_version"], 1)
        for stage in ("已核定", "执行中", "已退款"):
            code, _ = self.call(
                "POST", f"/orders/{order['id']}/refunds/{refund['id']}/advance",
                {"stage": stage}, role="案件审核员", actor="监管财务")
            self.assertEqual(code, 200)
        _, tracking = self.call("GET", f"/orders/{order['id']}/refunds")
        self.assertEqual(tracking["refunds"][0]["status"], "已退款")
        self.assertEqual(len(tracking["refunds"][0]["stage_history"]), 4)

    # ------------------------------------------------------- 案件隔离
    def test_evidence_isolation_and_ruling_over_http(self):
        _, complaint = self.call("POST", "/complaints", {
            "content": "说能治头疼", "complainant": "赵某",
            "subject_id": self.subject["id"], "service_id": self.service["id"]})
        _, case = self.call("POST", "/cases", {
            "subject_id": self.subject["id"], "service_id": self.service["id"],
            "complaint_ids": [complaint["id"]]}, role="巡查人员", actor="巡查员")
        cid = case["id"]
        self.call("POST", f"/cases/{cid}/evidence", {
            "bucket": "巡查证据", "content": "现场海报含治疗用语"},
            role="巡查人员", actor="巡查员")
        self.call("POST", f"/cases/{cid}/evidence", {
            "bucket": "消费者证据", "content": "聊天截图"},
            role="消费者", actor="赵某")
        self.call("POST", f"/cases/{cid}/evidence", {
            "bucket": "商家申辩", "content": "外包误发"},
            role="经营者", actor="商家")
        # 消费者不能提交巡查证据
        code, _ = self.call("POST", f"/cases/{cid}/evidence", {
            "bucket": "巡查证据", "content": "伪造"},
            role="消费者", actor="赵某")
        self.assertEqual(code, 403)
        # 商家只能看到自己的申辩
        _, merchant_view = self.call(
            "GET", f"/cases/{cid}/evidence", {}, role="经营者")
        self.assertEqual({e["bucket"] for e in merchant_view}, {"商家申辩"})
        # 审核员可见全部
        _, officer_view = self.call(
            "GET", f"/cases/{cid}/evidence", {}, role="案件审核员")
        self.assertEqual(len(officer_view), 3)
        # 系统不自动定罪：案件无 ruling，只有审核员能认定
        _, case_data = self.call("GET", f"/cases/{cid}")
        self.assertIsNone(case_data["ruling"])
        code, _ = self.call("POST", f"/cases/{cid}/ruling",
                            {"ruling": "虚假宣传"}, role="巡查人员", actor="巡查员")
        self.assertEqual(code, 403)
        self.call("POST", f"/cases/{cid}/ruling",
                  {"ruling": "构成虚假宣传"}, role="案件审核员", actor="审核员甲")

    # --------------------------------------------------- 改名跨区发现
    def test_alias_discovery_requires_regulator(self):
        code, _ = self.call(
            "GET", f"/subjects/{self.subject['id']}/aliases", role="经营者")
        self.assertEqual(code, 403)
        self.call("POST", "/subjects", {
            "name": "成都山月工作室", "unified_code": "", "region": "成都",
            "contact_phones": []})
        # 第一主体无电话，名称近似仍可命中
        _, findings = self.call(
            "GET", f"/subjects/{self.subject['id']}/aliases", role="巡查人员")
        self.assertTrue(findings["matches"])

    # --------------------------------------------------------- 规则版本
    def test_region_rule_versioning_over_http(self):
        code, _ = self.call("POST", "/rules/versions", {
            "version": "HZ-2026-9", "effective_from": "2026-09-01",
            "region": "杭州", "service_requirements": {"能量课程": ["新增备案X"]},
        }, role="经营者")
        self.assertEqual(code, 403)
        code, body = self.call("POST", "/rules/versions", {
            "version": "HZ-2026-9", "effective_from": "2026-09-01",
            "region": "杭州", "service_requirements": {"能量课程": ["新增备案X"]},
        }, role="案件审核员")
        self.assertEqual(code, 201)
        _, svc = self.call("POST", f"/services/{self.service['id']}/evaluate", {})
        self.assertEqual(svc["status"], "补充材料")
        self.assertIn("HZ-2026-9", svc["rule_versions"])


if __name__ == "__main__":
    unittest.main()
