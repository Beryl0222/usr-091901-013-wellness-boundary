"""疗愈服务边界治理核心领域。

覆盖：经营主体/从业人员/服务申报、规则状态判定、人工审查、
三方责任确认、合同版本与套餐生命周期（拆分/转赠/取消/未消费退款）、
投诉案件（证据字段隔离、重复投诉关联但不自动定罪）、
历史依据追溯、处置影响分析、改名跨区主体发现。

所有写操作留痕；判定与退款一律引用当时有效的规则版本与原合同版本。
"""

from dataclasses import dataclass, field, is_dataclass
from datetime import datetime

from rules import (
    DECISION_MANUAL_REVIEW,
    DECISION_PUBLISHABLE,
    DECISION_SUPPLEMENT,
    DECISION_VIOLATION,
    default_rulebook,
)

# 服务发布状态（与领域契约一致）
STATUS_DRAFT = "待申报"
STATUS_SUPPLEMENT = "补充材料"
STATUS_PUBLISHED = "允许发布"
STATUS_RESTRICTED = "限制经营"

# 案件状态
CASE_OPEN = "投诉处理中"
CASE_CLOSED = "已结案"

# 三方角色
PARTY_PLATFORM = "线上平台"
PARTY_HOTEL = "酒店"
PARTY_PROVIDER = "实际提供者"
RESPONSIBLE_PARTIES = (PARTY_PLATFORM, PARTY_HOTEL, PARTY_PROVIDER)

# 证据来源桶——同一案件、字段相互隔离
EVIDENCE_INSPECTION = "巡查证据"
EVIDENCE_CONSUMER = "消费者证据"
EVIDENCE_DEFENSE = "商家申辩"
EVIDENCE_BUCKETS = (EVIDENCE_INSPECTION, EVIDENCE_CONSUMER, EVIDENCE_DEFENSE)
# 各角色可见的证据桶；商家只能看到自己的申辩与巡查结论性内容以外的隔离设计
_BUCKET_VISIBILITY = {
    "巡查人员": (EVIDENCE_INSPECTION,),
    "消费者": (EVIDENCE_CONSUMER,),
    "经营者": (EVIDENCE_DEFENSE,),
    "案件审核员": EVIDENCE_BUCKETS,
}

# 退款执行阶段
REFUND_REQUESTED = "已申请"
REFUND_APPROVED = "已核定"
REFUND_PROCESSING = "执行中"
REFUND_DONE = "已退款"
REFUND_REJECTED = "已驳回"


def _now():
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Record:
    """带创建时间的记录基类。"""
    id: str
    created_at: str = field(default_factory=_now)

    def to_dict(self):
        return {k: _safe(v) for k, v in self.__dict__.items()}


def _safe(value):
    if is_dataclass(value):
        return {k: _safe(v) for k, v in value.__dict__.items()}
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return value


@dataclass
class Subject(Record):
    """经营主体。曾用名、联系方式与从业关联用于改名跨区发现。"""
    name: str = ""
    unified_code: str = ""          # 统一社会信用代码
    region: str = ""
    aliases: list = field(default_factory=list)
    contact_phones: list = field(default_factory=list)
    status: str = "在营"


@dataclass
class Practitioner(Record):
    name: str = ""
    practitioner_type: str = ""
    credentials: list = field(default_factory=list)
    subject_id: str = ""
    region: str = ""


@dataclass
class ClaimReview(Record):
    """一条宣传用语的判定记录（含历史快照）。"""
    service_id: str = ""
    text: str = ""
    evidence_documents: list = field(default_factory=list)
    result: dict = field(default_factory=dict)          # ReviewResult.to_dict
    # 人工审查
    manual_status: str = "无需审查"                      # 无需审查/待审查/审查中/维持限制/审查通过
    reviewer: str | None = None
    review_note: str | None = None
    reviewed_at: str | None = None

    @property
    def needs_manual(self):
        return self.result.get("decision") == DECISION_VIOLATION or \
            any(h["decision"] == DECISION_MANUAL_REVIEW for h in self.result.get("hits", []))


@dataclass
class ServiceDeclaration(Record):
    subject_id: str = ""
    name: str = ""
    service_type: str = ""
    region: str = ""
    steps: list = field(default_factory=list)
    credentials: list = field(default_factory=list)
    price: float = 0.0
    on_sale: bool = False
    status: str = STATUS_DRAFT
    status_reasons: list = field(default_factory=list)   # 判定依据快照
    rule_versions: list = field(default_factory=list)
    evaluated_on: str | None = None


@dataclass
class ResponsibilityConfirmation(Record):
    """三方对同一服务/合同分别确认责任；任一方未确认即不完整。"""
    service_id: str = ""
    contract_id: str = ""
    party: str = ""
    confirmed: bool = False
    confirmer: str | None = None
    note: str | None = None


@dataclass
class ContractVersion:
    """合同版本不可变；任何条款变更生成新版本，旧版本永久保留。"""
    version: int
    created_at: str
    terms: dict
    superseded_by: int | None = None


@dataclass
class PackageOrder(Record):
    """预付套餐订单。拆分、转赠、取消、未消费退款全部留痕并引用原合同。"""
    service_id: str = ""
    subject_id: str = ""
    contract_id: str = ""
    buyer: str = ""
    # 套餐条目: [{name, total, consumed, price}]
    items: list = field(default_factory=list)
    total_amount: float = 0.0
    status: str = "正常"                                # 正常/部分转赠/已取消/已退清
    transfers: list = field(default_factory=list)
    splits: list = field(default_factory=list)
    refunds: list = field(default_factory=list)
    events: list = field(default_factory=list)


@dataclass
class Contract(Record):
    service_id: str = ""
    subject_id: str = ""
    title: str = ""
    versions: list = field(default_factory=list)       # list[ContractVersion]
    current_version: int = 0

    def version_at(self, on: str):
        """取某时点有效的合同版本（on 为 ISO 时间，按 created_at 比较）。"""
        valid = [v for v in self.versions if v.created_at <= on]
        return max(valid, key=lambda v: v.version) if valid else None


@dataclass
class Complaint(Record):
    """单条投诉：可与案件关联，但关联本身不构成违法认定。"""
    case_id: str | None = None
    subject_id: str | None = None
    service_id: str | None = None
    order_id: str | None = None
    content: str = ""
    complainant: str = ""


@dataclass
class Evidence(Record):
    case_id: str = ""
    bucket: str = ""
    submitter: str = ""
    content: str = ""
    attachments: list = field(default_factory=list)


@dataclass
class Case(Record):
    """案件汇聚巡查、消费者证据与商家申辩；证据字段按来源隔离。"""
    case_no: str = ""
    subject_id: str | None = None
    service_id: str | None = None
    claim_review_id: str | None = None
    order_id: str | None = None
    status: str = CASE_OPEN
    complaint_ids: list = field(default_factory=list)
    evidence_ids: list = field(default_factory=list)
    # 违法认定只能由审核员作出；系统从不凭重复投诉自动判定
    ruling: str | None = None
    ruling_officer: str | None = None
    ruling_at: str | None = None
    handling_actions: list = field(default_factory=list)


class GovernanceError(Exception):
    """业务规则违例。"""


class GovernanceSystem:
    def __init__(self, rulebook=None, clock=_now):
        self.rules = rulebook or default_rulebook()
        self._clock = clock
        self._seq = 0
        self.subjects = {}
        self.practitioners = {}
        self.services = {}
        self.claims = {}
        self.confirmations = []
        self.contracts = {}
        self.orders = {}
        self.cases = {}
        self.complaints = {}
        self.evidence = {}
        # 审查队列：claim_id 入队即等待人工
        self.review_queue = []

    # ------------------------------------------------------------------ id
    def _id(self, prefix):
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    def _now(self):
        return self._clock()

    # ------------------------------------------------------- 经营主体/人员
    def register_subject(self, name, unified_code, region, contact_phones=(), aliases=()):
        if unified_code and any(s.unified_code == unified_code for s in self.subjects.values()):
            existing = next(s for s in self.subjects.values() if s.unified_code == unified_code)
            # 同一信用代码在新地区出现：合并名称线索而非新建
            existing.aliases = sorted(set(existing.aliases) | {name} | set(aliases))
            existing.contact_phones = sorted(set(existing.contact_phones) | set(contact_phones))
            return existing
        sid = self._id("SUB")
        subject = Subject(
            id=sid, created_at=self._now(), name=name, unified_code=unified_code,
            region=region, aliases=list(aliases), contact_phones=list(contact_phones),
        )
        self.subjects[sid] = subject
        return subject

    def register_practitioner(self, name, practitioner_type, credentials, subject_id, region):
        if subject_id not in self.subjects:
            raise GovernanceError(f"主体不存在: {subject_id}")
        pid = self._id("PRC")
        p = Practitioner(
            id=pid, created_at=self._now(), name=name, practitioner_type=practitioner_type,
            credentials=list(credentials), subject_id=subject_id, region=region,
        )
        self.practitioners[pid] = p
        return p

    # ------------------------------------------------------------- 服务申报
    def declare_service(self, subject_id, name, service_type, region, steps=(),
                        credentials=(), price=0.0, claims=(), claim_evidence=None):
        if subject_id not in self.subjects:
            raise GovernanceError(f"主体不存在: {subject_id}")
        sid = self._id("SVC")
        svc = ServiceDeclaration(
            id=sid, created_at=self._now(), subject_id=subject_id, name=name,
            service_type=service_type, region=region, steps=list(steps),
            credentials=list(credentials), price=price,
        )
        self.services[sid] = svc
        for text in claims:
            self.add_claim(sid, text, evidence_documents=(claim_evidence or {}).get(text, ()))
        self.evaluate_service(sid)
        return svc

    def add_claim(self, service_id, text, evidence_documents=()):
        svc = self.services.get(service_id)
        if svc is None:
            raise GovernanceError(f"服务不存在: {service_id}")
        cid = self._id("CLM")
        result = self.rules.review_claim(
            text, evidence_documents=evidence_documents,
            region=svc.region, on=datetime.fromisoformat(self._now()).date(),
        )
        review = ClaimReview(
            id=cid, created_at=self._now(), service_id=service_id, text=text,
            evidence_documents=list(evidence_documents), result=result.to_dict(),
        )
        if review.needs_manual:
            review.manual_status = "待审查"
            self.review_queue.append(cid)
        self.claims[cid] = review
        self.evaluate_service(service_id)
        return review

    def evaluate_service(self, service_id):
        """依据当前规则重新计算服务状态，并保存判定快照。"""
        svc = self.services[service_id]
        on = datetime.fromisoformat(self._now()).date()
        hits = []
        for p in self.practitioners.values():
            if p.subject_id == svc.subject_id and p.region == svc.region:
                hits.extend(self.rules.check_practitioner(
                    p.practitioner_type, p.credentials, svc.region, on))
        hits.extend(self.rules.check_service(
            svc.service_type, svc.credentials, svc.region, on, svc.steps))
        claim_reviews = [c for c in self.claims.values() if c.service_id == service_id]
        for c in claim_reviews:
            # 人工审查通过的主张，其自动命中不再限制发布（但快照仍保留）
            if c.manual_status == "审查通过":
                continue
            for h in c.result.get("hits", []):
                hits.append(_SnapshotHit(h))
        versions = set()
        for h in hits:
            versions.add(h.rule_version)
        for c in claim_reviews:
            versions.update(c.result.get("rule_versions", []))

        decisions = {h.decision for h in hits}
        pending_manual = any(c.manual_status in ("待审查", "审查中") for c in claim_reviews)
        upheld_claims = [c for c in claim_reviews
                         if c.result.get("decision") == DECISION_VIOLATION
                         and c.manual_status == "维持限制"]
        if DECISION_VIOLATION in decisions or upheld_claims or pending_manual:
            svc.status = STATUS_RESTRICTED
        elif DECISION_SUPPLEMENT in decisions:
            svc.status = STATUS_SUPPLEMENT
        else:
            svc.status = STATUS_PUBLISHED
        # 已允许发布的服务可由经营者上架；被限制/补证时强制下架可见性
        if svc.status != STATUS_PUBLISHED:
            svc.on_sale = False
        svc.status_reasons = [h.to_dict() if hasattr(h, "to_dict") else _SnapshotHit(h).to_dict()
                              for h in hits]
        svc.rule_versions = sorted(versions)
        svc.evaluated_on = on.isoformat()
        return svc

    def set_on_sale(self, service_id, on_sale):
        svc = self.services[service_id]
        if on_sale and svc.status != STATUS_PUBLISHED:
            raise GovernanceError(f"服务状态为「{svc.status}」，不允许上架发布")
        svc.on_sale = on_sale
        return svc

    # ------------------------------------------------------- 人工审查
    def take_review(self, reviewer, claim_id=None):
        """审核员领取疾病治疗主张审查任务。"""
        if claim_id is None:
            claim_id = next((c for c in self.review_queue
                             if self.claims[c].manual_status == "待审查"), None)
            if claim_id is None:
                return None
        review = self.claims.get(claim_id)
        if review is None or not review.needs_manual:
            raise GovernanceError("该宣传无需人工审查")
        review.manual_status = "审查中"
        review.reviewer = reviewer
        if claim_id in self.review_queue:
            self.review_queue.remove(claim_id)
        return review

    def resolve_review(self, claim_id, approve, note, officer):
        """人工审查结论：通过需给出依据；驳回则维持限制。系统不替人判断。"""
        review = self.claims[claim_id]
        if review.manual_status not in ("待审查", "审查中"):
            raise GovernanceError("该审查任务已处理")
        review.manual_status = "审查通过" if approve else "维持限制"
        review.review_note = note
        review.reviewer = officer
        review.reviewed_at = self._now()
        self.evaluate_service(review.service_id)
        return review

    # ------------------------------------------------------- 三方责任确认
    def confirm_responsibility(self, service_id, party, confirmer, contract_id=None, note=None):
        if party not in RESPONSIBLE_PARTIES:
            raise GovernanceError(f"未知责任方: {party}")
        existing = next((c for c in self.confirmations
                         if c.service_id == service_id and c.party == party), None)
        if existing:
            existing.confirmed = True
            existing.confirmer = confirmer
            existing.note = note
            existing.contract_id = contract_id
        else:
            self.confirmations.append(ResponsibilityConfirmation(
                id=self._id("RSP"), created_at=self._now(), service_id=service_id,
                contract_id=contract_id or "", party=party, confirmed=True,
                confirmer=confirmer, note=note,
            ))
        return self.responsibility_status(service_id)

    def responsibility_status(self, service_id):
        """分别返回三方确认情况，未确认方不得隐去。"""
        result = {}
        for party in RESPONSIBLE_PARTIES:
            c = next((c for c in self.confirmations
                      if c.service_id == service_id and c.party == party and c.confirmed), None)
            result[party] = {
                "confirmed": c is not None,
                "confirmer": c.confirmer if c else None,
                "note": c.note if c else None,
            }
        result["all_confirmed"] = all(v["confirmed"] for v in result.values())
        return result

    # ------------------------------------------------------- 合同与套餐
    def create_contract(self, service_id, title, terms):
        svc = self.services.get(service_id)
        if svc is None:
            raise GovernanceError(f"服务不存在: {service_id}")
        cid = self._id("CTR")
        contract = Contract(
            id=cid, created_at=self._now(), service_id=service_id,
            subject_id=svc.subject_id, title=title,
            current_version=1,
            versions=[ContractVersion(version=1, created_at=self._now(), terms=dict(terms))],
        )
        self.contracts[cid] = contract
        return contract

    def amend_contract(self, contract_id, new_terms):
        """条款修订：旧版本标记被替代但永不删除。"""
        contract = self.contracts[contract_id]
        old = contract.versions[-1]
        new_no = old.version + 1
        old.superseded_by = new_no
        contract.versions.append(ContractVersion(
            version=new_no, created_at=self._now(), terms=dict(new_terms)))
        contract.current_version = new_no
        return contract

    def create_order(self, service_id, buyer, items, contract_id):
        svc = self.services[service_id]
        if svc.status != STATUS_PUBLISHED:
            raise GovernanceError(f"服务状态为「{svc.status}」，不得销售预付套餐")
        if contract_id not in self.contracts:
            raise GovernanceError(f"合同不存在: {contract_id}")
        if self.contracts[contract_id].service_id != service_id:
            raise GovernanceError("合同与服务不匹配")
        resp = self.responsibility_status(service_id)
        if not resp["all_confirmed"]:
            raise GovernanceError("三方责任未分别确认，不得销售预付套餐")
        total = round(sum(i["total"] * i["price"] for i in items), 2)
        oid = self._id("ORD")
        order = PackageOrder(
            id=oid, created_at=self._now(), service_id=service_id, subject_id=svc.subject_id,
            contract_id=contract_id, buyer=buyer,
            items=[dict(i, consumed=0) for i in items], total_amount=total,
        )
        order.events.append({"at": self._now(), "type": "下单",
                             "contract_version": self.contracts[contract_id].current_version})
        self.orders[oid] = order
        return order

    def _active_contract_version(self, order):
        """订单的处置永远引用订单成立时的原合同版本（首版）。"""
        return self.contracts[order.contract_id].versions[0]

    def consume(self, order_id, item_name, quantity):
        order = self.orders[order_id]
        for it in order.items:
            if it["name"] == item_name:
                if it["consumed"] + quantity > it["total"]:
                    raise GovernanceError("消费数量超出套餐余量")
                it["consumed"] += quantity
                order.events.append({"at": self._now(), "type": "消费",
                                     "item": item_name, "quantity": quantity})
                return order
        raise GovernanceError(f"套餐条目不存在: {item_name}")

    def split_package(self, order_id, item_name, quantity, new_buyer, operator):
        """套餐拆分：拆出部分成立独立子订单，仍引用原合同版本。"""
        order = self.orders[order_id]
        src = next((i for i in order.items if i["name"] == item_name), None)
        if src is None or src["total"] - src["consumed"] < quantity:
            raise GovernanceError("可拆分余量不足")
        src["total"] -= quantity
        child = PackageOrder(
            id=self._id("ORD"), created_at=self._now(), service_id=order.service_id,
            subject_id=order.subject_id, contract_id=order.contract_id, buyer=new_buyer,
            items=[{"name": item_name, "total": quantity, "consumed": 0, "price": src["price"]}],
            total_amount=round(quantity * src["price"], 2),
        )
        child.events.append({"at": self._now(), "type": "拆分子单",
                             "parent_order": order_id, "operator": operator,
                             "contract_version": self._active_contract_version(order).version})
        self.orders[child.id] = child
        record = {"at": self._now(), "to_order": child.id, "item": item_name,
                  "quantity": quantity, "new_buyer": new_buyer, "operator": operator}
        order.splits.append(record)
        order.events.append({"at": self._now(), "type": "拆分", **record})
        return child

    def transfer_package(self, order_id, item_name, quantity, recipient, operator):
        """转赠：仅变更使用人，不改变合同关系与原合同版本。"""
        order = self.orders[order_id]
        src = next((i for i in order.items if i["name"] == item_name), None)
        if src is None or src["total"] - src["consumed"] < quantity:
            raise GovernanceError("可转赠余量不足")
        record = {"at": self._now(), "item": item_name, "quantity": quantity,
                  "recipient": recipient, "operator": operator,
                  "contract_version": self._active_contract_version(order).version}
        order.transfers.append(record)
        order.events.append({"at": self._now(), "type": "转赠", **record})
        return record

    def cancel_order(self, order_id, operator, reason):
        order = self.orders[order_id]
        if order.status == "已取消":
            raise GovernanceError("订单已取消")
        order.status = "已取消"
        order.events.append({"at": self._now(), "type": "取消", "operator": operator,
                             "reason": reason,
                             "contract_version": self._active_contract_version(order).version})
        return order

    def unconsumed_refund(self, order_id, operator, note=""):
        """未消费退款：按原合同价格计算未消费金额，分阶段留痕可追踪。"""
        order = self.orders[order_id]
        contract_v = self._active_contract_version(order)
        amount = round(sum((i["total"] - i["consumed"]) * i["price"] for i in order.items), 2)
        if amount <= 0:
            raise GovernanceError("没有可退的未消费金额")
        refund = {
            "id": self._id("RFD"), "at": self._now(), "operator": operator,
            "amount": amount, "status": REFUND_REQUESTED, "note": note,
            "contract_version": contract_v.version,
            "contract_terms": contract_v.terms.get("refund_policy"),
            "stage_history": [{"at": self._now(), "stage": REFUND_REQUESTED}],
        }
        order.refunds.append(refund)
        order.events.append({"at": self._now(), "type": "退款申请",
                             "amount": amount, "contract_version": contract_v.version})
        return refund

    def advance_refund(self, order_id, refund_id, stage, actor):
        order = self.orders[order_id]
        refund = next((r for r in order.refunds if r["id"] == refund_id), None)
        if refund is None:
            raise GovernanceError("退款记录不存在")
        refund["status"] = stage
        refund["stage_history"].append({"at": self._now(), "stage": stage, "actor": actor})
        order.events.append({"at": self._now(), "type": "退款进展",
                             "refund_id": refund_id, "stage": stage})
        if stage == REFUND_DONE and order.status != "已取消":
            remaining = sum((i["total"] - i["consumed"]) * i["price"] for i in order.items)
            if remaining <= refund["amount"] + 0.009:
                order.status = "已退清"
        return refund

    # ------------------------------------------------------------- 投诉案件
    def file_complaint(self, content, complainant, subject_id=None, service_id=None, order_id=None):
        cid = self._id("CMP")
        complaint = Complaint(
            id=cid, created_at=self._now(), content=content, complainant=complainant,
            subject_id=subject_id, service_id=service_id, order_id=order_id,
        )
        self.complaints[cid] = complaint
        return complaint

    def open_case(self, subject_id=None, service_id=None, claim_review_id=None, order_id=None,
                  complaint_ids=()):
        case_id = self._id("CASE")
        case = Case(
            id=case_id, created_at=self._now(), case_no=case_id.replace("CASE", "案"),
            subject_id=subject_id, service_id=service_id,
            claim_review_id=claim_review_id, order_id=order_id,
        )
        self.cases[case_id] = case
        for cid in complaint_ids:
            self.link_complaint(case_id, cid)
        return case

    def link_complaint(self, case_id, complaint_id):
        """关联重复投诉：仅用于并案分析，绝不直接产生违法结论。"""
        case = self.cases[case_id]
        complaint = self.complaints[complaint_id]
        if complaint_id in case.complaint_ids:
            return case
        case.complaint_ids.append(complaint_id)
        complaint.case_id = case_id
        return case

    def add_evidence(self, case_id, bucket, submitter, content, attachments=()):
        if bucket not in EVIDENCE_BUCKETS:
            raise GovernanceError(f"证据类型必须是: {EVIDENCE_BUCKETS}")
        eid = self._id("EVD")
        ev = Evidence(id=eid, created_at=self._now(), case_id=case_id, bucket=bucket,
                      submitter=submitter, content=content, attachments=list(attachments))
        self.evidence[eid] = ev
        self.cases[case_id].evidence_ids.append(eid)
        return ev

    def view_evidence(self, case_id, viewer_role):
        """按角色隔离证据字段：非审核员只能看到本来源桶。"""
        allowed = set(_BUCKET_VISIBILITY.get(viewer_role, ()))
        return [self.evidence[eid].to_dict()
                for eid in self.cases[case_id].evidence_ids
                if self.evidence[eid].bucket in allowed]

    def issue_ruling(self, case_id, ruling, officer):
        """违法/违规认定只能由案件审核员人工作出。"""
        case = self.cases[case_id]
        case.ruling = ruling
        case.ruling_officer = officer
        case.ruling_at = self._now()
        return case

    def close_case(self, case_id, officer, handling_action=None):
        case = self.cases[case_id]
        case.status = CASE_CLOSED
        if handling_action:
            case.handling_actions.append({"at": self._now(), "action": handling_action,
                                          "officer": officer})
        return case

    def restrict_service(self, service_id, case_id, reason, officer):
        """处置动作：下架限制，并记录影响范围。"""
        svc = self.services[service_id]
        svc.status = STATUS_RESTRICTED
        svc.on_sale = False
        svc.status_reasons.append({"decision": DECISION_VIOLATION, "reason": reason,
                                   "rule_version": "人工处置", "rule_region": svc.region,
                                   "rule_key": f"case:{case_id}"})
        case = self.cases[case_id]
        case.handling_actions.append({"at": self._now(), "action": "限制发布/下架",
                                      "service_id": service_id, "officer": officer,
                                      "reason": reason})
        affected = self.impact_of_service(service_id)
        return {"action": "限制发布/下架", "service_id": service_id, "affected": affected}

    # ------------------------------------------------------------- 追溯分析
    def explain_claim(self, claim_id):
        """说明某条宣传在当时为何被限制：还原判定当日规则与命中条目。"""
        review = self.claims[claim_id]
        on = review.result["evaluated_on"]
        from datetime import date as _date
        region = review.result["region"]
        versions = self.rules.applicable_versions(region, _date.fromisoformat(on))
        return {
            "claim_id": claim_id,
            "text": review.text,
            "decision_at": review.created_at,
            "decision": review.result["decision"],
            "evaluated_on": on,
            "region": region,
            "rule_versions_in_force": [
                {"version": v.version, "region": v.region,
                 "effective_from": v.effective_from, "effective_to": v.effective_to}
                for v in versions
            ],
            "hits": review.result["hits"],
            "manual_review": {
                "status": review.manual_status,
                "reviewer": review.reviewer,
                "note": review.review_note,
                "reviewed_at": review.reviewed_at,
            },
            "note": "即使此后规则修订，本结论仍以判定当日生效规则为准",
        }

    def impact_of_service(self, service_id):
        """处置影响哪些在售服务：同主体、同类型或同宣传用语的在售服务。"""
        target = self.services[service_id]
        target_claims = {c.text for c in self.claims.values() if c.service_id == service_id}
        impacted = []
        for svc in self.services.values():
            if svc.id == service_id:
                continue
            if not svc.on_sale:
                continue
            shared_claims = target_claims & {c.text for c in self.claims.values()
                                             if c.service_id == svc.id}
            if svc.subject_id == target.subject_id and (
                svc.service_type == target.service_type or shared_claims
            ):
                impacted.append({
                    "service_id": svc.id, "name": svc.name,
                    "reason": "同主体同类型服务" if svc.service_type == target.service_type
                    else "使用相同宣传用语",
                    "shared_claims": sorted(shared_claims),
                    "on_sale": svc.on_sale,
                })
        orders = [{"order_id": o.id, "buyer": o.buyer, "status": o.status,
                   "refunds": [{"id": r["id"], "amount": r["amount"], "status": r["status"]}
                               for r in o.refunds]}
                  for o in self.orders.values() if o.service_id == service_id]
        return {"service": {"id": target.id, "name": target.name, "status": target.status,
                            "on_sale": target.on_sale},
                "other_on_sale_services": impacted, "related_orders": orders}

    def refund_tracking(self, order_id):
        """退款执行到哪一步。"""
        order = self.orders[order_id]
        return {
            "order_id": order_id,
            "contract_id": order.contract_id,
            "original_contract_version": self._active_contract_version(order).version,
            "order_status": order.status,
            "refunds": order.refunds,
            "events": order.events,
        }

    # ----------------------------------------------------- 改名跨区主体发现
    def find_alias_subjects(self, subject_id):
        """发现同一主体换名跨区继续经营。

        线索：相同统一信用代码（注册时已合并）、相同联系电话、
                高度相似名称、共用从业人员。
        """
        target = self.subjects[subject_id]
        target_names = {target.name, *target.aliases}
        shared_practitioners = {p.id for p in self.practitioners.values()
                                if p.subject_id == subject_id}
        findings = []
        for s in self.subjects.values():
            if s.id == subject_id:
                continue
            clues = []
            if target.unified_code and s.unified_code == target.unified_code:
                clues.append("同一统一社会信用代码")
            if set(target.contact_phones) & set(s.contact_phones):
                clues.append("共用联系电话:" + ",".join(
                    sorted(set(target.contact_phones) & set(s.contact_phones))))
            other_names = {s.name, *s.aliases}
            for n1 in target_names:
                for n2 in other_names:
                    if n1 != n2 and _name_similar(n1, n2):
                        clues.append(f"名称近似:「{n1}」≈「{n2}」")
            shared = shared_practitioners & {p.id for p in self.practitioners.values()
                                             if p.subject_id == s.id}
            if shared:
                clues.append("共用从业人员:" + ",".join(sorted(shared)))
            if clues:
                findings.append({
                    "subject_id": s.id, "name": s.name, "region": s.region,
                    "aliases": s.aliases, "clues": clues,
                })
        return {
            "subject_id": subject_id, "name": target.name, "region": target.region,
            "known_aliases": target.aliases, "matches": findings,
            "note": "命中为疑似关联线索，需人工核查确认，不自动等同主体",
        }


class _SnapshotHit:
    """把规则命中 dict 适配为统一 to_dict 输出。"""

    def __init__(self, data):
        self._data = dict(data)

    def __getattr__(self, item):
        return self._data[item]

    def to_dict(self):
        return dict(self._data)


def _name_similar(a, b):
    """简化的名称相似度：去常见后缀后包含，或字符集合重合度高。"""
    tails = ("有限公司", "股份有限公司", "工作室", "管理", "健康", "文化", "科技",
             "（", "）", "(", ")")
    core_a, core_b = a, b
    for t in tails:
        core_a = core_a.replace(t, "")
        core_b = core_b.replace(t, "")
    if not core_a or not core_b:
        return False
    if core_a in core_b or core_b in core_a:
        return True
    overlap = len(set(core_a) & set(core_b))
    return overlap / max(len(set(core_a)), len(set(core_b))) >= 0.8
