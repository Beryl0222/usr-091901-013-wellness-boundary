"""监管解释：宣传受限原因、处置影响范围、退款进度与换名跨区线索。"""

from .evaluation import evaluate_service
from .util import parse_time


def explain_claim(store, claim_id, at=None):
    """说明某条宣传在指定时间为何被限制：当时命中的规则版本与人工审查结论。

    优先取指定时间之前的评估留痕；没有留痕时按当时生效的规则试算，不落库。
    """
    claim = store.get("claims", claim_id)
    evaluations = [e for e in store.all("evaluations") if e["service_id"] == claim["service_id"]]
    chosen = None
    if at is None:
        if evaluations:
            chosen = max(evaluations, key=lambda e: parse_time(e["at"]))
    else:
        past = [e for e in evaluations if parse_time(e["at"]) <= parse_time(at)]
        chosen = max(past, key=lambda e: parse_time(e["at"])) if past else None
    if chosen is None:
        chosen = evaluate_service(store, claim["service_id"], at=at, persist=False)
    hits = [h for h in chosen["hits"] if h.get("claim_id") == claim_id]
    review = claim["review"]
    if review and at is not None and parse_time(review["at"]) > parse_time(at):
        review = None  # 指定时间之后才作出的审查结论不计入
    return {"claim_id": claim_id, "text": claim["text"], "current_status": claim["status"],
            "evaluation_at": chosen["at"], "service_status_at": chosen["status"],
            "restricted": bool(hits), "hits": hits,
            "rule_snapshot": chosen["rule_snapshot"], "manual_review": review}


def disposition_impact(store, disposition_id):
    """处置影响了哪些在售服务及其当前状态。"""
    disposition = store.get("dispositions", disposition_id)
    services = [store.get("services", service_id) for service_id in disposition["affected_service_ids"]]
    case = store.get("cases", disposition["case_id"])
    return {"disposition": disposition, "affected_services": services, "case_status": case["status"]}


def refund_progress(store, refund_id):
    """退款执行到哪一步：完整时间线、当前步骤与对应合同版本。"""
    refund_order = store.get("refunds", refund_id)
    contract = store.get("contracts", refund_order["contract_id"])
    return {"refund": refund_order, "current_step": refund_order["steps"][-1]["step"],
            "completed": refund_order["status"] == "已完成", "timeline": refund_order["steps"],
            "contract_version": refund_order["contract_version"], "contract_status": contract["status"]}


def renamed_subject_leads(store):
    """同一主体换名跨区经营的线索：名称不同但强身份标识一致。"""
    leads = []
    subjects = store.all("subjects")
    for index, first in enumerate(subjects):
        for second in subjects[index + 1:]:
            if first["name"] == second["name"]:
                continue
            signals = []
            if first["credit_code"] == second["credit_code"]:
                signals.append("统一社会信用代码相同")
            if first["legal_person_id"] == second["legal_person_id"]:
                signals.append("法定代表人证件相同")
            if first["contact_phone"] == second["contact_phone"]:
                signals.append("联系电话相同")
            strong = [s for s in signals if s != "联系电话相同"]
            if not strong:
                continue
            leads.append({"subject_ids": [first["id"], second["id"]],
                          "names": [first["name"], second["name"]],
                          "regions": [first["region"], second["region"]],
                          "statuses": [first["status"], second["status"]],
                          "cross_region": first["region"] != second["region"],
                          "signals": signals})
    return leads
