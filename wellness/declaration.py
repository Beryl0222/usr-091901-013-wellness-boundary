"""经营者申报：经营主体、从业人员、服务项目与宣传用语。"""

from .util import DomainError, now_iso

SERVICE_CATEGORIES = ("音声疗愈", "水晶疗愈", "禅修旅修", "线上能量课程", "其他体验")


def declare_subject(store, *, name, credit_code, legal_person, legal_person_id,
                    region, contact_phone, address):
    """申报经营主体。"""
    if not name or not credit_code:
        raise DomainError("主体名称与统一社会信用代码不能为空")
    subject = {
        "id": store.next_id("sub"),
        "name": name,
        "credit_code": credit_code,
        "legal_person": legal_person,
        "legal_person_id": legal_person_id,
        "region": region,
        "contact_phone": contact_phone,
        "address": address,
        "status": "正常",
        "created_at": now_iso(),
    }
    return store.insert("subjects", subject)


def declare_practitioner(store, *, subject_id, name, id_number, credentials=()):
    """申报从业人员及其资质证书。"""
    store.get("subjects", subject_id)
    practitioner = {
        "id": store.next_id("prac"),
        "subject_id": subject_id,
        "name": name,
        "id_number": id_number,
        "credentials": [dict(c) for c in credentials],
        "created_at": now_iso(),
    }
    return store.insert("practitioners", practitioner)


def declare_service(store, *, subject_id, name, category, region, steps, price,
                    claims=(), channels=()):
    """申报服务项目：服务步骤、价格与宣传用语一并提交，等待准入评估。"""
    store.get("subjects", subject_id)
    if not steps:
        raise DomainError("服务步骤不能为空")
    if "amount" not in price:
        raise DomainError("价格必须包含 amount 字段")
    service = {
        "id": store.next_id("svc"),
        "subject_id": subject_id,
        "name": name,
        "category": category,
        "region": region,
        "steps": list(steps),
        "price": dict(price),
        "channels": list(channels),
        "status": "待申报",
        "claim_ids": [],
        "evaluation_ids": [],
        "created_at": now_iso(),
    }
    store.insert("services", service)
    for text in claims:
        claim = {
            "id": store.next_id("clm"),
            "service_id": service["id"],
            "text": text,
            "status": "待评估",
            "review": None,
            "submitted_at": now_iso(),
        }
        store.insert("claims", claim)
        service["claim_ids"].append(claim["id"])
    return service
