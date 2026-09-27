"""准入评估：对照当时当地生效的准入规则与负面清单，标出服务与宣传用语状态。

状态优先级：越界 > 待人工审查 > 补充材料 > 允许发布。
疾病治疗类宣传一律转人工审查（领域不变量），人工结论在后续评估中保持效力。
"""

from .rules import active_admission_rules, active_negative_entries
from .util import DomainError, now_iso, parse_time

DISEASE_CLAIM_PATTERNS = ("治疗", "治愈", "疗效", "根治", "消炎", "抗癌", "处方", "诊断", "临床", "病理")

STATUS_PRIORITY = {"允许发布": 0, "补充材料": 1, "待人工审查": 2, "越界": 3}


def _held_credentials(store, subject_id, at):
    """主体全部从业人员在指定时间仍有效的资质类型。"""
    held = set()
    for practitioner in store.find("practitioners", subject_id=subject_id):
        for credential in practitioner["credentials"]:
            valid_until = credential.get("valid_until")
            if valid_until is None or parse_time(valid_until) >= parse_time(at):
                held.add(credential["type"])
    return held


def evaluate_service(store, service_id, at=None, persist=True):
    """评估服务在指定时间的准入状态；persist 为假时只做试算不留痕。"""
    at = at or now_iso()
    service = store.get("services", service_id)
    rules = active_admission_rules(store, service["region"], service["category"], at)
    entries = active_negative_entries(store, service["region"], service["category"], at)
    hits, status = [], "允许发布"

    def escalate(target):
        nonlocal status
        if STATUS_PRIORITY[target] > STATUS_PRIORITY[status]:
            status = target

    held = _held_credentials(store, service["subject_id"], at)
    for rule in rules:
        missing = [c for c in rule["required_credentials"] if c not in held]
        if missing:
            escalate("补充材料")
            hits.append({"source": "准入规则", "ref_id": rule["id"], "version": rule["version"],
                         "detail": f"缺少有效资质：{'、'.join(missing)}"})

    step_text = "；".join(service["steps"])
    for entry in entries:
        if entry["kind"] == "服务步骤" and entry["pattern"] in step_text:
            escalate("越界" if entry["severity"] == "越界" else "待人工审查")
            hits.append({"source": "负面清单", "ref_id": entry["id"], "version": entry["version"],
                         "detail": f"服务步骤命中「{entry['pattern']}」"})
        elif entry["kind"] == "预付捆绑" and service["price"].get("prepaid"):
            if float(service["price"]["amount"]) > float(entry["pattern"]):
                escalate("越界" if entry["severity"] == "越界" else "待人工审查")
                hits.append({"source": "负面清单", "ref_id": entry["id"], "version": entry["version"],
                             "detail": f"预付金额超过上限 {entry['pattern']} 元"})

    claim_results = {}
    for claim_id in service["claim_ids"]:
        claim = store.get("claims", claim_id)
        review = claim["review"]
        if review and parse_time(review["at"]) <= parse_time(at):
            # 人工审查结论在后续评估中保持效力
            if review["decision"] == "限制":
                escalate("越界")
                hits.append({"source": "人工审查", "ref_id": claim_id, "version": 1,
                             "detail": f"宣传用语经人工审查限制：{review['note'] or '未注明'}",
                             "claim_id": claim_id})
                claim_results[claim_id] = "被限制"
            else:
                claim_results[claim_id] = "已通过"
            continue
        claim_status = "已通过"
        for entry in entries:
            if entry["kind"] == "宣传用语" and entry["pattern"] in claim["text"]:
                if entry["severity"] == "越界":
                    escalate("越界")
                    claim_status = "被限制"
                elif claim_status != "被限制":
                    escalate("待人工审查")
                    claim_status = "待人工审查"
                hits.append({"source": "负面清单", "ref_id": entry["id"], "version": entry["version"],
                             "detail": f"宣传用语命中「{entry['pattern']}」", "claim_id": claim_id})
        for pattern in DISEASE_CLAIM_PATTERNS:
            if pattern in claim["text"]:
                escalate("待人工审查")
                hits.append({"source": "疾病宣传筛查", "ref_id": "builtin-disease", "version": 1,
                             "detail": f"疑似疾病治疗主张「{pattern}」，转人工审查", "claim_id": claim_id})
                if claim_status == "已通过":
                    claim_status = "待人工审查"
                break
        claim_results[claim_id] = claim_status

    evaluation = {
        "id": store.next_id("eva") if persist else None,
        "service_id": service_id,
        "at": at,
        "status": status,
        "hits": hits,
        "rule_snapshot": [{"rule_id": r["id"], "version": r["version"], "note": r["note"]} for r in rules]
                         + [{"rule_id": e["id"], "version": e["version"], "note": e["note"]} for e in entries],
    }
    if persist:
        store.insert("evaluations", evaluation)
        service["evaluation_ids"].append(evaluation["id"])
        if service["status"] != "限制经营":
            service["status"] = status
        for claim_id, claim_status in claim_results.items():
            store.get("claims", claim_id)["status"] = claim_status
    return evaluation


def review_claim(store, claim_id, *, reviewer, decision, note="", at=None):
    """人工审查宣传用语；结论为 通过 或 限制，之后重新汇总服务状态。"""
    if decision not in ("通过", "限制"):
        raise DomainError("审查结论只能是 通过 或 限制")
    claim = store.get("claims", claim_id)
    claim["review"] = {"reviewer": reviewer, "decision": decision, "note": note, "at": at or now_iso()}
    claim["status"] = "已通过" if decision == "通过" else "被限制"
    evaluate_service(store, claim["service_id"])
    return claim
