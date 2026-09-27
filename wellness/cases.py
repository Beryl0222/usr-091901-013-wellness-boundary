"""投诉与巡查案件：巡查记录、消费者证据、商家申辩分区隔离，重复投诉仅作关联。"""

from .responsibility import on_sale_services
from .util import DomainError, Forbidden, now_iso

COMPARTMENTS = ("巡查记录", "消费者证据", "商家申辩")
WRITERS = {"巡查人员": "巡查记录", "消费者": "消费者证据", "经营者": "商家申辩"}
REVIEWER = "案件审核员"


def open_case(store, *, source, subject_id, opened_by, service_id=None, at=None):
    """立案：来源为 巡查 或 投诉。"""
    if source not in ("巡查", "投诉"):
        raise DomainError("案件来源只能是 巡查 或 投诉")
    store.get("subjects", subject_id)
    case = {"id": store.next_id("case"), "source": source, "subject_id": subject_id,
            "service_id": service_id, "status": "投诉处理中",
            "compartments": {name: [] for name in COMPARTMENTS}, "links": [],
            "disposition_id": None, "opened_by": opened_by,
            "opened_at": at or now_iso(), "closed_at": None}
    return store.insert("cases", case)


def add_material(store, case_id, *, role, author, title, content, at=None):
    """按角色把材料写入对应分区；角色之间不能越权代写。"""
    case = store.get("cases", case_id)
    if case["status"] == "已结案":
        raise DomainError("案件已结案，不能再提交材料")
    compartment = WRITERS.get(role)
    if compartment is None:
        raise Forbidden(f"{role} 不能提交案件材料")
    entry = {"author": author, "title": title, "content": content, "at": at or now_iso()}
    case["compartments"][compartment].append(entry)
    return {"compartment": compartment, "entry": entry}


def read_case(store, case_id, *, role):
    """按角色读取案件：审核员可见全部分区，其余角色只能看到自己所在分区。"""
    case = store.get("cases", case_id)
    view = {key: case[key] for key in ("id", "source", "subject_id", "service_id", "status",
                                       "links", "disposition_id", "opened_at", "closed_at")}
    if role == REVIEWER:
        view["compartments"] = case["compartments"]
    else:
        compartment = WRITERS.get(role)
        if compartment is None:
            raise Forbidden(f"{role} 不能查阅案件材料")
        view["compartments"] = {compartment: case["compartments"][compartment]}
    return view


def link_cases(store, case_id, other_case_id, *, reason, at=None):
    """关联重复投诉：只记录线索，不改变任何一方的状态与定性。"""
    if case_id == other_case_id:
        raise DomainError("案件不能与自身关联")
    case = store.get("cases", case_id)
    other = store.get("cases", other_case_id)
    at = at or now_iso()
    case["links"].append({"case_id": other_case_id, "reason": reason, "at": at})
    other["links"].append({"case_id": case_id, "reason": reason, "at": at})
    return {"case_id": case_id, "linked": other_case_id, "reason": reason}


def close_case(store, case_id, *, reviewer, decision, rule_refs=(), affect_on_sale=True, at=None):
    """审核员结案：定性必须明确给出，处置引用规则版本并记录受影响的在售服务。"""
    case = store.get("cases", case_id)
    if case["status"] == "已结案":
        raise DomainError("案件已结案")
    if not decision:
        raise DomainError("结案必须给出明确处理决定")
    at = at or now_iso()
    affected = []
    if affect_on_sale:
        for service in on_sale_services(store):
            if service["subject_id"] == case["subject_id"]:
                service["status"] = "限制经营"
                affected.append(service["id"])
    disposition = {"id": store.next_id("disp"), "case_id": case_id, "decision": decision,
                   "rule_refs": list(rule_refs), "affected_service_ids": affected,
                   "by": reviewer, "at": at}
    store.insert("dispositions", disposition)
    case["status"] = "已结案"
    case["closed_at"] = at
    case["disposition_id"] = disposition["id"]
    return disposition
