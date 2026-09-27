"""疗愈服务边界治理的服务入口：健康检查、领域契约与治理接口。"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from wellness import cases, contracts, declaration, evaluation, oversight, responsibility, rules
from wellness.store import Store
from wellness.util import DomainError, NotFound

SERVICE_ID = "wellness-boundary"
SERVICE_NAME = "疗愈服务边界治理"
CONTRACT_PATH = Path(__file__).with_name("domain_contract.json")


def load_contract():
    """读取并校验项目领域契约。"""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if contract.get("service_id") != SERVICE_ID:
        raise ValueError("领域契约与服务身份不一致")
    return contract


def health_payload():
    """返回服务运行状态。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


def _service_detail(store, service_id):
    service = dict(store.get("services", service_id))
    service["claims"] = store.find("claims", service_id=service_id)
    evaluations = store.find("evaluations", service_id=service_id)
    service["latest_evaluation"] = evaluations[-1] if evaluations else None
    service["responsibility"] = responsibility.responsibility_of(store, service_id)
    service["on_sale"] = any(s["id"] == service_id for s in responsibility.on_sale_services(store))
    return service


def _responsibility_view(store, service_id):
    record = responsibility.responsibility_of(store, service_id)
    if record is None:
        raise NotFound("该服务尚未发起责任确认")
    return record


ROUTES = [
    ("GET", "/health", lambda s, p, q, b: health_payload()),
    ("GET", "/contract", lambda s, p, q, b: load_contract()),
    ("POST", "/subjects", lambda s, p, q, b: declaration.declare_subject(s, **b)),
    ("GET", "/subjects", lambda s, p, q, b: {"subjects": s.all("subjects")}),
    ("POST", "/practitioners", lambda s, p, q, b: declaration.declare_practitioner(s, **b)),
    ("POST", "/services", lambda s, p, q, b: declaration.declare_service(s, **b)),
    ("GET", "/services/{id}", lambda s, p, q, b: _service_detail(s, p["id"])),
    ("POST", "/services/{id}/evaluate", lambda s, p, q, b: evaluation.evaluate_service(s, p["id"], at=b.get("at"))),
    ("POST", "/claims/{id}/review", lambda s, p, q, b: evaluation.review_claim(s, p["id"], **b)),
    ("POST", "/rules/admission", lambda s, p, q, b: rules.add_admission_rule(s, **b)),
    ("POST", "/rules/negative", lambda s, p, q, b: rules.add_negative_entry(s, **b)),
    ("POST", "/responsibility/{service_id}/confirm", lambda s, p, q, b: responsibility.confirm_party(s, p["service_id"], **b)),
    ("GET", "/responsibility/{service_id}", lambda s, p, q, b: _responsibility_view(s, p["service_id"])),
    ("POST", "/contracts", lambda s, p, q, b: contracts.create_contract(s, **b)),
    ("GET", "/contracts/{id}", lambda s, p, q, b: s.get("contracts", p["id"])),
    ("POST", "/contracts/{id}/consume", lambda s, p, q, b: contracts.record_consumption(s, p["id"], b["units"])),
    ("POST", "/contracts/{id}/split", lambda s, p, q, b: contracts.split_contract(s, p["id"], b["split_units"])),
    ("POST", "/contracts/{id}/transfer", lambda s, p, q, b: contracts.transfer_contract(s, p["id"], b["new_holder"])),
    ("POST", "/contracts/{id}/cancel", lambda s, p, q, b: contracts.cancel_contract(s, p["id"], reason=b.get("reason", ""), refund=b.get("refund", True))),
    ("POST", "/contracts/{id}/refund", lambda s, p, q, b: contracts.request_refund(s, p["id"], reason=b.get("reason", ""))),
    ("GET", "/refunds/{id}", lambda s, p, q, b: oversight.refund_progress(s, p["id"])),
    ("POST", "/refunds/{id}/advance", lambda s, p, q, b: contracts.advance_refund(s, p["id"], note=b.get("note", ""), reject=b.get("reject", False))),
    ("POST", "/cases", lambda s, p, q, b: cases.open_case(s, **b)),
    ("GET", "/cases/{id}", lambda s, p, q, b: cases.read_case(s, p["id"], role=q.get("role", ""))),
    ("POST", "/cases/{id}/materials", lambda s, p, q, b: cases.add_material(s, p["id"], **b)),
    ("POST", "/cases/{id}/links", lambda s, p, q, b: cases.link_cases(s, p["id"], b["other_case_id"], reason=b["reason"])),
    ("POST", "/cases/{id}/close", lambda s, p, q, b: cases.close_case(s, p["id"], **b)),
    ("GET", "/oversight/claims/{id}/explanation", lambda s, p, q, b: oversight.explain_claim(s, p["id"], at=q.get("at"))),
    ("GET", "/oversight/dispositions/{id}/impact", lambda s, p, q, b: oversight.disposition_impact(s, p["id"])),
    ("GET", "/oversight/renamed-subjects", lambda s, p, q, b: {"leads": oversight.renamed_subject_leads(s)}),
]


def _match(pattern, path):
    pattern_parts = pattern.strip("/").split("/")
    path_parts = path.strip("/").split("/")
    if len(pattern_parts) != len(path_parts):
        return None
    params = {}
    for want, got in zip(pattern_parts, path_parts):
        if want.startswith("{") and want.endswith("}"):
            params[want[1:-1]] = got
        elif want != got:
            return None
    return params


def dispatch(store, method, path, query, body):
    """按路由表分发请求，未知路径抛 NotFound。"""
    for route_method, pattern, handler in ROUTES:
        if route_method != method:
            continue
        params = _match(pattern, path)
        if params is not None:
            return handler(store, params, query, body)
    raise NotFound(f"未知路径：{method} {path}")


class Handler(BaseHTTPRequestHandler):
    """JSON 接口处理器；业务异常映射为对应状态码。"""

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        try:
            parsed = urlparse(self.path)
            query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            body = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
            store = getattr(self.server, "store", None)
            if store is None:
                store = self.server.store = Store()
            payload = dispatch(store, method, parsed.path, query, body)
            if method == "POST":
                self._save_snapshot(store)
            self._send_json(payload)
        except DomainError as error:
            self._send_json({"error": str(error)}, status=error.status)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            self._send_json({"error": f"请求不合法：{error}"}, status=400)

    def _save_snapshot(self, store):
        path = getattr(self.server, "data_path", None)
        if path:
            Path(path).write_text(json.dumps(store.snapshot(), ensure_ascii=False), encoding="utf-8")

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def create_server(port, store=None, host="0.0.0.0", data_path=None):
    """创建 HTTP 服务；可注入仓储与快照路径。"""
    server = ThreadingHTTPServer((host, port), Handler)
    server.store = store or Store()
    server.data_path = data_path
    return server


def main():
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--data", help="快照文件路径，用于跨重启保留数据")
    args = parser.parse_args()
    if args.check:
        contract = load_contract()
        assert contract["states"] and contract["invariants"]
        print("基础检查通过")
        return
    store = Store()
    if args.data and Path(args.data).exists():
        store = Store.restore(json.loads(Path(args.data).read_text(encoding="utf-8")))
    create_server(args.port, store, data_path=args.data).serve_forever()


if __name__ == "__main__":
    main()
