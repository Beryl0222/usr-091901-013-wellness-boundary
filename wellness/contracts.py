"""预付合同：拆分、转赠、取消与未消费退款，历次变更保留原合同版本。"""

from .util import DomainError, now_iso

REFUND_STEPS = ("申请", "审核", "执行", "到账")


def _snapshot(contract):
    return {"consumer": contract["consumer"], "units": contract["units"],
            "unit_price": contract["unit_price"], "consumed_units": contract["consumed_units"],
            "status": contract["status"]}


def _append_version(contract, change, at):
    contract["versions"].append({"version": len(contract["versions"]) + 1, "at": at,
                                 "change": change, "snapshot": _snapshot(contract)})


def _remaining(contract):
    return contract["units"] - contract["consumed_units"]


def _usable(store, contract_id):
    contract = store.get("contracts", contract_id)
    if contract["status"] != "有效":
        raise DomainError(f"合同当前状态为 {contract['status']}，不能执行该操作")
    return contract


def create_contract(store, *, consumer, service_id, units, unit_price, terms, at=None):
    """订立预付合同，生成第 1 版合同快照。"""
    store.get("services", service_id)
    if units <= 0 or unit_price < 0:
        raise DomainError("合同份额与单价不合法")
    at = at or now_iso()
    contract = {"id": store.next_id("ctr"), "consumer": consumer, "service_id": service_id,
                "units": units, "unit_price": unit_price, "consumed_units": 0,
                "terms": terms, "status": "有效", "origin": None, "versions": [], "created_at": at}
    _append_version(contract, "订立", at)
    return store.insert("contracts", contract)


def record_consumption(store, contract_id, units, at=None):
    """登记消费份额。"""
    contract = _usable(store, contract_id)
    if units <= 0 or units > _remaining(contract):
        raise DomainError("消费数量超出合同剩余份额")
    contract["consumed_units"] += units
    _append_version(contract, f"消费 {units} 份", at or now_iso())
    return contract


def split_contract(store, contract_id, split_units, at=None):
    """把未消费份额拆分为多份子合同，子合同引用原合同及其拆分时的版本。"""
    contract = _usable(store, contract_id)
    at = at or now_iso()
    if not split_units or any(u <= 0 for u in split_units) or sum(split_units) != _remaining(contract):
        raise DomainError("拆分份额必须为正且合计等于未消费数量")
    origin = {"contract_id": contract["id"], "version": len(contract["versions"])}
    children = []
    for units in split_units:
        child = {"id": store.next_id("ctr"), "consumer": contract["consumer"],
                 "service_id": contract["service_id"], "units": units,
                 "unit_price": contract["unit_price"], "consumed_units": 0,
                 "terms": contract["terms"], "status": "有效", "origin": origin,
                 "versions": [], "created_at": at}
        _append_version(child, f"自合同 {contract['id']} 第 {origin['version']} 版拆分", at)
        store.insert("contracts", child)
        children.append(child)
    contract["status"] = "已拆分"
    _append_version(contract, f"拆分为 {len(children)} 份子合同", at)
    return children


def transfer_contract(store, contract_id, new_holder, at=None):
    """转赠合同；原持有人保留在历史版本中。"""
    contract = _usable(store, contract_id)
    if not new_holder or new_holder == contract["consumer"]:
        raise DomainError("受让人须为不同的持有人")
    contract["consumer"] = new_holder
    _append_version(contract, f"转赠给 {new_holder}", at or now_iso())
    return contract


def cancel_contract(store, contract_id, *, reason="", refund=True, at=None):
    """取消合同；有未消费份额时默认同时发起退款。"""
    contract = _usable(store, contract_id)
    at = at or now_iso()
    contract["status"] = "已取消"
    _append_version(contract, f"取消（{reason}）" if reason else "取消", at)
    refund_order = None
    if refund and _remaining(contract) > 0:
        refund_order = request_refund(store, contract_id, reason=reason or "取消后退还未消费金额", at=at)
    return {"contract": contract, "refund": refund_order}


def request_refund(store, contract_id, *, reason="", at=None):
    """按未消费金额申请退款，退款单记录对应的合同版本。"""
    contract = store.get("contracts", contract_id)
    amount = _remaining(contract) * contract["unit_price"]
    if amount <= 0:
        raise DomainError("无未消费金额可退")
    if any(r["status"] == "执行中" for r in store.find("refunds", contract_id=contract_id)):
        raise DomainError("该合同已有进行中的退款单")
    at = at or now_iso()
    refund_order = {"id": store.next_id("rfd"), "contract_id": contract_id,
                    "contract_version": len(contract["versions"]), "amount": amount,
                    "reason": reason, "status": "执行中", "resume_status": contract["status"],
                    "steps": [{"step": REFUND_STEPS[0], "at": at, "note": reason}], "created_at": at}
    store.insert("refunds", refund_order)
    contract["status"] = "退款中"
    _append_version(contract, "申请退款", at)
    return refund_order


def advance_refund(store, refund_id, *, note="", reject=False, at=None):
    """把退款单推进一步（审核→执行→到账），仅审核环节可驳回。"""
    refund_order = store.get("refunds", refund_id)
    if refund_order["status"] != "执行中":
        raise DomainError("退款单已结束")
    at = at or now_iso()
    contract = store.get("contracts", refund_order["contract_id"])
    if reject:
        if REFUND_STEPS[len(refund_order["steps"])] != "审核":
            raise DomainError("仅审核环节可驳回")
        refund_order["steps"].append({"step": "驳回", "at": at, "note": note})
        refund_order["status"] = "已驳回"
        contract["status"] = refund_order["resume_status"]
        _append_version(contract, "退款被驳回", at)
        return refund_order
    next_step = REFUND_STEPS[len(refund_order["steps"])]
    refund_order["steps"].append({"step": next_step, "at": at, "note": note})
    if next_step == REFUND_STEPS[-1]:
        refund_order["status"] = "已完成"
        contract["consumed_units"] = contract["units"]
        contract["status"] = "已退完"
        _append_version(contract, "退款到账", at)
    return refund_order
