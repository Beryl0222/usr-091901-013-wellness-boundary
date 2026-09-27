"""疗愈服务边界治理的服务入口。

在 /health、/contract 基础上提供治理后端 JSON 接口：
主体/人员/服务申报、规则判定、人工审查、三方责任确认、
合同套餐生命周期、投诉案件与追溯分析。

调用身份通过请求头 X-Actor-Role / X-Actor-Id 或报文中的
role/actor 字段提供；敏感处置动作限定监管角色。
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from governance import (
    EVIDENCE_BUCKETS,
    RESPONSIBLE_PARTIES,
    GovernanceError,
    GovernanceSystem,
)
from rules import RuleVersion, default_rulebook

SERVICE_ID = "wellness-boundary"
SERVICE_NAME = "疗愈服务边界治理"
CONTRACT_PATH = Path(__file__).with_name("domain_contract.json")

REGULATOR_ROLES = {"案件审核员", "巡查人员"}
# 责任方与系统角色的对应：谁确认谁的责任，不能代签
_PARTY_ROLES = {
    "线上平台": {"渠道平台", "线上平台"},
    "酒店": {"酒店"},
    "实际提供者": {"经营者", "实际提供者"},
}
_BUCKET_ROLES = {
    "巡查证据": {"巡查人员", "案件审核员"},
    "消费者证据": {"消费者"},
    "商家申辩": {"经营者"},
}


def load_contract():
    """读取并校验项目领域契约。"""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if contract.get("service_id") != SERVICE_ID:
        raise ValueError("领域契约与服务身份不一致")
    return contract


def health_payload():
    """返回服务运行状态。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


class Api:
    """把 HTTP 请求分派到治理领域，并集中处理角色约束。"""

    def __init__(self):
        self.system = GovernanceSystem(rulebook=default_rulebook())
        self.lock = threading.Lock()

    # -- 工具 -----------------------------------------------------------
    @staticmethod
    def _actor(payload, headers):
        return (
            payload.get("actor") or headers.get("x-actor-id") or "匿名",
            payload.get("role") or headers.get("x-actor-role") or "",
        )

    @staticmethod
    def _require(role, allowed, action):
        if role not in allowed:
            raise PermissionError(f"{action}需要角色: {sorted(allowed)}（当前: {role or '无'}）")

    # -- 路由 -----------------------------------------------------------
    def handle(self, method, path, query, payload, headers):
        p = [x for x in path.strip("/").split("/") if x != ""]
        sys = self.system

        # 集合查询
        if method == "GET" and len(p) == 1:
            if p[0] == "subjects":
                return 200, [s.to_dict() for s in sys.subjects.values()]
            if p[0] == "services":
                return 200, [s.to_dict() for s in sys.services.values()]
            if p[0] == "cases":
                return 200, [c.to_dict() for c in sys.cases.values()]
            if p[0] == "complaints":
                return 200, [c.to_dict() for c in sys.complaints.values()]
            if p[0] == "rules":
                return 200, {"versions": [v.__dict__ for v in sys.rules.applicable_versions(
                    query.get("region", ["全国"])[0])]}
        if method == "POST" and p == ["rules", "versions"]:
            _, role = self._actor(payload, headers)
            self._require(role, REGULATOR_ROLES, "发布规则版本")
            v = RuleVersion(
                version=payload["version"], effective_from=payload["effective_from"],
                effective_to=payload.get("effective_to"), region=payload.get("region", "全国"),
                practitioner_requirements={k: tuple(v) for k, v in
                                           payload.get("practitioner_requirements", {}).items()},
                service_requirements={k: tuple(v) for k, v in
                                      payload.get("service_requirements", {}).items()},
                prohibited_claims=tuple(payload.get("prohibited_claims", ())),
                prohibited_steps=tuple(payload.get("prohibited_steps", ())),
                efficacy_claims=tuple(payload.get("efficacy_claims", ())),
            )
            with self.lock:
                sys.rules.add_version(v)
            return 201, {"version": v.version}

        # 主体
        if method == "POST" and p == ["subjects"]:
            with self.lock:
                s = sys.register_subject(
                    payload["name"], payload.get("unified_code", ""), payload["region"],
                    payload.get("contact_phones", ()), payload.get("aliases", ()))
            return 201, s.to_dict()
        if method == "GET" and len(p) == 2 and p[0] == "subjects":
            s = sys.subjects.get(p[1])
            if s is None:
                return 404, {"error": "主体不存在"}
            return 200, s.to_dict()
        if method == "GET" and len(p) == 3 and p[0] == "subjects" and p[2] == "aliases":
            if p[1] not in sys.subjects:
                return 404, {"error": "主体不存在"}
            _, role = self._actor(payload, headers)
            self._require(role, REGULATOR_ROLES, "改名跨区关联分析")
            return 200, sys.find_alias_subjects(p[1])

        # 从业人员
        if method == "POST" and p == ["practitioners"]:
            with self.lock:
                pr = sys.register_practitioner(
                    payload["name"], payload["practitioner_type"],
                    payload.get("credentials", ()), payload["subject_id"], payload["region"])
            return 201, pr.to_dict()

        # 服务申报与状态
        if method == "POST" and p == ["services"]:
            with self.lock:
                svc = sys.declare_service(
                    payload["subject_id"], payload["name"], payload["service_type"],
                    payload["region"], payload.get("steps", ()),
                    payload.get("credentials", ()), payload.get("price", 0.0),
                    payload.get("claims", ()), payload.get("claim_evidence"))
            return 201, svc.to_dict()
        if method == "GET" and len(p) == 2 and p[0] == "services":
            svc = sys.services.get(p[1])
            if svc is None:
                return 404, {"error": "服务不存在"}
            data = svc.to_dict()
            data["claims"] = [c.to_dict() for c in sys.claims.values() if c.service_id == svc.id]
            data["responsibility"] = sys.responsibility_status(svc.id)
            return 200, data
        if method == "POST" and len(p) == 3 and p[0] == "services" and p[2] == "claims":
            with self.lock:
                c = sys.add_claim(p[1], payload["text"], payload.get("evidence_documents", ()))
            return 201, c.to_dict()
        if method == "POST" and len(p) == 3 and p[0] == "services" and p[2] == "evaluate":
            with self.lock:
                svc = sys.evaluate_service(p[1])
            return 200, svc.to_dict()
        if method == "POST" and len(p) == 3 and p[0] == "services" and p[2] == "sale":
            with self.lock:
                svc = sys.set_on_sale(p[1], bool(payload["on_sale"]))
            return 200, svc.to_dict()
        if method == "GET" and len(p) == 3 and p[0] == "services" and p[2] == "impact":
            if p[1] not in sys.services:
                return 404, {"error": "服务不存在"}
            _, role = self._actor(payload, headers)
            self._require(role, REGULATOR_ROLES, "处置影响分析")
            return 200, sys.impact_of_service(p[1])

        # 三方责任分别确认
        if len(p) == 3 and p[0] == "services" and p[2] == "responsibility":
            if method == "GET":
                if p[1] not in sys.services:
                    return 404, {"error": "服务不存在"}
                return 200, sys.responsibility_status(p[1])
            if method == "POST":
                actor, role = self._actor(payload, headers)
                party = payload["party"]
                if party not in RESPONSIBLE_PARTIES:
                    return 400, {"error": f"责任方必须是: {RESPONSIBLE_PARTIES}"}
                self._require(role, _PARTY_ROLES[party], f"确认「{party}」责任")
                with self.lock:
                    status = sys.confirm_responsibility(
                        p[1], party, actor, payload.get("contract_id"), payload.get("note"))
                return 200, status

        # 人工审查
        if method == "POST" and p == ["reviews", "take"]:
            actor, role = self._actor(payload, headers)
            self._require(role, {"案件审核员"}, "领取人工审查")
            with self.lock:
                r = sys.take_review(actor, payload.get("claim_id"))
            return (200, r.to_dict()) if r else (200, {"message": "队列已空"})
        if method == "POST" and len(p) == 3 and p[0] == "reviews" and p[2] == "resolve":
            actor, role = self._actor(payload, headers)
            self._require(role, {"案件审核员"}, "作出人工审查结论")
            with self.lock:
                r = sys.resolve_review(p[1], bool(payload["approve"]),
                                       payload.get("note", ""), actor)
            return 200, r.to_dict()
        if method == "GET" and len(p) == 3 and p[0] == "claims" and p[2] == "explain":
            if p[1] not in sys.claims:
                return 404, {"error": "宣传记录不存在"}
            return 200, sys.explain_claim(p[1])

        # 合同与套餐
        if method == "POST" and p == ["contracts"]:
            with self.lock:
                c = sys.create_contract(payload["service_id"], payload["title"], payload["terms"])
            return 201, c.to_dict()
        if method == "POST" and len(p) == 3 and p[0] == "contracts" and p[2] == "amend":
            with self.lock:
                c = sys.amend_contract(p[1], payload["terms"])
            return 200, c.to_dict()
        if method == "POST" and p == ["orders"]:
            with self.lock:
                o = sys.create_order(payload["service_id"], payload["buyer"],
                                     payload["items"], payload["contract_id"])
            return 201, o.to_dict()

        order_actions = {
            "consume": lambda: sys.consume(p[1], payload["item"], int(payload["quantity"])),
            "cancel": lambda: sys.cancel_order(p[1], actor, payload.get("reason", "")),
            "split": lambda: sys.split_package(p[1], payload["item"], int(payload["quantity"]),
                                               payload["new_buyer"], actor),
            "transfer": lambda: sys.transfer_package(p[1], payload["item"],
                                                     int(payload["quantity"]),
                                                     payload["recipient"], actor),
            "refund": lambda: sys.unconsumed_refund(p[1], actor, payload.get("note", "")),
        }
        if method == "POST" and len(p) == 3 and p[0] == "orders" and p[2] in order_actions:
            actor, _ = self._actor(payload, headers)
            with self.lock:
                result = order_actions[p[2]]()
            return 200, result.to_dict() if hasattr(result, "to_dict") else result
        if method == "POST" and len(p) == 5 and p[0] == "orders" and p[2] == "refunds" \
                and p[4] == "advance":
            actor, role = self._actor(payload, headers)
            self._require(role, REGULATOR_ROLES | {"经营者"}, "推进退款执行")
            with self.lock:
                r = sys.advance_refund(p[1], p[3], payload["stage"], actor)
            return 200, r
        if method == "GET" and len(p) == 3 and p[0] == "orders" and p[2] == "refunds":
            if p[1] not in sys.orders:
                return 404, {"error": "订单不存在"}
            return 200, sys.refund_tracking(p[1])
        if method == "GET" and len(p) == 2 and p[0] == "orders":
            o = sys.orders.get(p[1])
            if o is None:
                return 404, {"error": "订单不存在"}
            return 200, o.to_dict()

        # 投诉与案件
        if method == "POST" and p == ["complaints"]:
            with self.lock:
                c = sys.file_complaint(
                    payload["content"], payload.get("complainant", "匿名"),
                    payload.get("subject_id"), payload.get("service_id"),
                    payload.get("order_id"))
            return 201, c.to_dict()
        if method == "POST" and p == ["cases"]:
            _, role = self._actor(payload, headers)
            self._require(role, REGULATOR_ROLES, "立案")
            with self.lock:
                c = sys.open_case(payload.get("subject_id"), payload.get("service_id"),
                                  payload.get("claim_review_id"), payload.get("order_id"),
                                  payload.get("complaint_ids", ()))
            return 201, c.to_dict()
        if method == "GET" and len(p) == 2 and p[0] == "cases":
            case = sys.cases.get(p[1])
            if case is None:
                return 404, {"error": "案件不存在"}
            data = case.to_dict()
            data["complaints"] = [sys.complaints[i].to_dict() for i in case.complaint_ids]
            return 200, data
        if method == "POST" and len(p) == 3 and p[0] == "cases" and p[2] == "complaints":
            _, role = self._actor(payload, headers)
            self._require(role, REGULATOR_ROLES, "关联投诉")
            with self.lock:
                case = sys.link_complaint(p[1], payload["complaint_id"])
            return 200, case.to_dict()
        if len(p) == 3 and p[0] == "cases" and p[2] == "evidence":
            if method == "POST":
                actor, role = self._actor(payload, headers)
                bucket = payload["bucket"]
                if bucket not in EVIDENCE_BUCKETS:
                    return 400, {"error": f"证据桶必须是: {EVIDENCE_BUCKETS}"}
                self._require(role, _BUCKET_ROLES[bucket], f"提交{bucket}")
                with self.lock:
                    ev = sys.add_evidence(p[1], bucket, actor, payload["content"],
                                          payload.get("attachments", ()))
                return 201, ev.to_dict()
            if method == "GET":
                _, role = self._actor(payload, headers)
                if p[1] not in sys.cases:
                    return 404, {"error": "案件不存在"}
                return 200, sys.view_evidence(p[1], role or query.get("role", [""])[0])
        if method == "POST" and len(p) == 3 and p[0] == "cases" and p[2] == "ruling":
            actor, role = self._actor(payload, headers)
            self._require(role, {"案件审核员"}, "作出违法违规认定")
            with self.lock:
                case = sys.issue_ruling(p[1], payload["ruling"], actor)
            return 200, case.to_dict()
        if method == "POST" and len(p) == 3 and p[0] == "cases" and p[2] == "close":
            actor, role = self._actor(payload, headers)
            self._require(role, {"案件审核员"}, "结案")
            with self.lock:
                case = sys.close_case(p[1], actor, payload.get("handling_action"))
            return 200, case.to_dict()
        if method == "POST" and len(p) == 3 and p[0] == "cases" and p[2] == "restrict":
            actor, role = self._actor(payload, headers)
            self._require(role, REGULATOR_ROLES, "限制服务")
            with self.lock:
                result = sys.restrict_service(payload["service_id"], p[1],
                                              payload["reason"], actor)
            return 200, result

        return 404, {"error": "未知路由"}


class Handler(BaseHTTPRequestHandler):
    """HTTP 适配：JSON 收发与异常到状态码的映射。"""

    api = Api()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, health_payload()); return
        if parsed.path == "/contract":
            self._send_json(200, load_contract()); return
        # GET 无请求体，角色等参数经查询字符串传入
        query = parse_qs(parsed.query)
        payload = {k: v[0] for k, v in query.items() if k in ("role", "actor")}
        self._dispatch("GET", parsed.path, query, payload)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": "请求体必须是 JSON 对象"}); return
        self._dispatch("POST", parsed.path, parse_qs(parsed.query), payload)

    def _dispatch(self, method, path, query, payload):
        try:
            status, body = self.api.handle(method, path, query, payload, self.headers)
        except PermissionError as exc:
            self._send_json(403, {"error": str(exc)})
        except GovernanceError as exc:
            self._send_json(400, {"error": str(exc)})
        except (KeyError, ValueError) as exc:
            self._send_json(400, {"error": f"请求不合法: {exc}"})
        else:
            self._send_json(status, body)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def main():
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        contract = load_contract()
        assert contract["states"] and contract["invariants"]
        # 基础规则判定必须可用，疾病主张必须被拦截并转人工
        probe = default_rulebook().review_claim("音声疗愈可以治疗抑郁症")
        assert probe.decision == "越界"
        assert any(h.decision == "转人工审查" for h in probe.hits)
        print("基础检查通过")
        return
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
