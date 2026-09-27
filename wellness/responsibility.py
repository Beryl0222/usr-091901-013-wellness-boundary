"""渠道平台、酒店与实际提供者的责任分别确认，三方齐备后服务才可在售。"""

from .util import DomainError, now_iso

PARTIES = ("渠道平台", "酒店", "实际提供者")


def _open(store, service_id, terms_version):
    record = {
        "id": store.next_id("resp"),
        "service_id": service_id,
        "terms_version": terms_version,
        "parties": {party: {"status": "待确认", "confirmer": None, "at": None} for party in PARTIES},
    }
    return store.insert("responsibilities", record)


def confirm_party(store, service_id, *, party, confirmer, terms_version, at=None):
    """一方按指定条款版本确认责任；条款版本不一致时要求按最新条款重新确认。"""
    store.get("services", service_id)
    if party not in PARTIES:
        raise DomainError(f"未知责任方：{party}（应为 {'、'.join(PARTIES)}）")
    records = store.find("responsibilities", service_id=service_id)
    record = records[0] if records else _open(store, service_id, terms_version)
    if record["terms_version"] != terms_version:
        raise DomainError("责任条款版本不一致，请按最新条款重新确认")
    record["parties"][party] = {"status": "已确认", "confirmer": confirmer,
                                "terms_version": terms_version, "at": at or now_iso()}
    return record


def responsibility_of(store, service_id):
    records = store.find("responsibilities", service_id=service_id)
    return records[0] if records else None


def all_confirmed(record):
    return all(party["status"] == "已确认" for party in record["parties"].values())


def on_sale_services(store):
    """当前在售服务：评估允许发布且三方责任均已确认。"""
    result = []
    for service in store.all("services"):
        if service["status"] != "允许发布":
            continue
        record = responsibility_of(store, service["id"])
        if record and all_confirmed(record):
            result.append(service)
    return result
