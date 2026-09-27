"""随地区与时间生效的准入规则与负面清单。

规则是治理判定的唯一依据。每条规则携带适用地区与生效/失效时间，
判定结果必须引用命中时有效的规则版本，以便日后回答
"某条宣传在当时为何被限制"。
"""

from dataclasses import dataclass
from datetime import date

# 判定结论
DECISION_PUBLISHABLE = "可发布"
DECISION_SUPPLEMENT = "需补证"
DECISION_MANUAL_REVIEW = "转人工审查"
DECISION_VIOLATION = "越界"

DECISIONS = (
    DECISION_PUBLISHABLE,
    DECISION_SUPPLEMENT,
    DECISION_MANUAL_REVIEW,
    DECISION_VIOLATION,
)

# 越界结论优先级最高：疾病治疗主张属禁止性负面清单，不允许自动放行；
# 但系统不得自动判定违法——"越界"只表示拦截发布并强制人工审查。
_SEVERITY = {
    DECISION_PUBLISHABLE: 0,
    DECISION_SUPPLEMENT: 1,
    DECISION_MANUAL_REVIEW: 2,
    DECISION_VIOLATION: 3,
}

# 疾病治疗主张关键词：命中即越界并强制人工审查。
_DISEASE_TERMS = (
    "治疗", "治愈", "疗效", "医治", "诊疗", "诊断", "处方",
    "抑郁症", "焦虑症", "失眠症", "高血压", "糖尿病", "肿瘤",
    "病症", "患者", "康复", "替代医疗", "包治", "断根", "根治",
    "医学治疗", "临床有效率",
)

# 功效性用语关键词：可宣传放松体验，但功效断言要求证据支撑。
_EFFICACY_TERMS = (
    "包治", "根治", "见效", "治愈率",
    "排毒", "疏通经络", "能量疗愈", "治愈创伤", "改变脑波",
    "治疗失眠", "缓解抑郁", "包好", "无效退款保证",
)

# 明显属于医疗行为的服务步骤（非医疗机构不得实施）。
_MEDICAL_STEP_TERMS = (
    "针刺", "注射", "开药", "放血", "灌肠",
    "心理诊断", "临床治疗", "电刺激治疗",
)


def today():
    return date.today()


@dataclass(frozen=True)
class RuleVersion:
    """规则清单的一个版本，整体在指定地区与时段生效。"""

    version: str
    effective_from: str          # ISO 日期，含当日
    effective_to: str | None     # None 表示长期有效
    region: str                  # "全国" 或具体地区
    practitioner_requirements: dict   # 人员类型 -> 必需资质集合
    service_requirements: dict        # 服务类型 -> 必需资质集合
    prohibited_claims: tuple          # 禁止性宣传负面清单
    prohibited_steps: tuple           # 禁止性服务步骤
    efficacy_claims: tuple            # 需证据支撑的功效用语

    def covers(self, region: str, on: date) -> bool:
        if self.region != "全国" and self.region != region:
            return False
        if on < date.fromisoformat(self.effective_from):
            return False
        if self.effective_to is not None and on > date.fromisoformat(self.effective_to):
            return False
        return True


@dataclass(frozen=True)
class Hit:
    """一次规则命中，完整记录判定当时的依据。"""

    decision: str
    reason: str
    rule_version: str
    rule_region: str
    rule_key: str

    def to_dict(self):
        return {
            "decision": self.decision,
            "reason": self.reason,
            "rule_version": self.rule_version,
            "rule_region": self.rule_region,
            "rule_key": self.rule_key,
        }


@dataclass(frozen=True)
class ReviewResult:
    """对一条宣传用语的判定结果。"""

    decision: str
    hits: tuple
    evaluated_on: str
    region: str
    rule_versions: tuple

    def to_dict(self):
        return {
            "decision": self.decision,
            "evaluated_on": self.evaluated_on,
            "region": self.region,
            "rule_versions": list(self.rule_versions),
            "hits": [h.to_dict() for h in self.hits],
        }


class RuleBook:
    """按地区与时间查询适用规则并执行宣传/准入判定。"""

    def __init__(self, versions=None):
        self._versions = list(versions or [])

    def add_version(self, version: RuleVersion):
        if any(v.version == version.version for v in self._versions):
            raise ValueError(f"规则版本已存在: {version.version}")
        self._versions.append(version)

    def applicable_versions(self, region: str, on: date | None = None):
        on = on or today()
        return [v for v in self._versions if v.covers(region, on)]

    @staticmethod
    def _requirements(versions, table, key):
        """多版本同时适用时取并集——叠加规则只能更严，不能放宽。"""
        required = set()
        for v in versions:
            if key in getattr(v, table):
                required.update(getattr(v, table)[key])
        return required

    @staticmethod
    def _cite(versions, table, key, held):
        """引用真正施加缺失项的规则版本；地区特定规则优先于全国规则。"""
        contributors = [
            v for v in versions
            if set(getattr(v, table).get(key, ())) - set(held or [])
        ]
        return next((v for v in contributors if v.region != "全国"), contributors[0])

    def check_practitioner(self, practitioner_type, held_credentials, region, on=None):
        """从业人员准入：返回缺失资质命中列表。"""
        on = on or today()
        versions = self.applicable_versions(region, on)
        missing = self._requirements(versions, "practitioner_requirements", practitioner_type) \
            - set(held_credentials or [])
        if not missing:
            return []
        cite = self._cite(versions, "practitioner_requirements", practitioner_type,
                          held_credentials)
        return [Hit(
            DECISION_SUPPLEMENT,
            f"从业人员类型「{practitioner_type}」缺少资质: {sorted(missing)}",
            cite.version, cite.region, f"practitioner:{practitioner_type}",
        )]

    def check_service(self, service_type, held_credentials, region, on=None, steps=()):
        """服务准入：资质要求 + 服务步骤负面清单。"""
        on = on or today()
        versions = self.applicable_versions(region, on)
        hits = []
        missing = self._requirements(versions, "service_requirements", service_type) \
            - set(held_credentials or [])
        if missing:
            cite = self._cite(versions, "service_requirements", service_type, held_credentials)
            hits.append(Hit(
                DECISION_SUPPLEMENT,
                f"服务类型「{service_type}」缺少资质/备案: {sorted(missing)}",
                cite.version, cite.region, f"service:{service_type}",
            ))
        for step in steps or []:
            term = next((t for t in _MEDICAL_STEP_TERMS if t in step), None)
            if term is None:
                continue
            cite = next((v for v in versions if term in v.prohibited_steps), None)
            if cite is not None:
                hits.append(Hit(
                    DECISION_VIOLATION,
                    f"服务步骤「{step}」涉及医疗行为，非医疗机构不得实施",
                    cite.version, cite.region, f"step:{term}",
                ))
        return hits

    def review_claim(self, text, evidence_documents=(), region=None, on=None):
        """判定一条宣传用语。

        - 命中疾病治疗主张 → 越界（禁止性清单）且强制转人工审查；
        - 功效断言无证据 → 需补证；
        - 其余 → 可发布。
        """
        on = on or today()
        region = region or "全国"
        versions = self.applicable_versions(region, on) or self.applicable_versions("全国", on)
        cite = versions[0] if versions else None
        cv = cite.version if cite else "基线规则"
        cr = cite.region if cite else "全国"
        hits = []
        matched_disease = [t for t in _DISEASE_TERMS if t in text]
        if matched_disease:
            hits.append(Hit(
                DECISION_VIOLATION,
                f"宣传含疾病治疗主张（{matched_disease}），属禁止性负面清单，不得发布",
                cv, cr, "claim:disease-treatment",
            ))
            hits.append(Hit(
                DECISION_MANUAL_REVIEW,
                "涉及疾病治疗的主张必须转人工审查，系统不得自动放行",
                cv, cr, "review:mandatory-human",
            ))
        matched_efficacy = [t for t in _EFFICACY_TERMS if t in text]
        if matched_efficacy and not evidence_documents:
            hits.append(Hit(
                DECISION_SUPPLEMENT,
                f"功效性用语（{matched_efficacy}）需上传证据材料后方可展示",
                cv, cr, "claim:efficacy-evidence",
            ))
        decision = DECISION_PUBLISHABLE
        for h in hits:
            if _SEVERITY[h.decision] > _SEVERITY[decision]:
                decision = h.decision
        return ReviewResult(
            decision=decision,
            hits=tuple(hits),
            evaluated_on=on.isoformat(),
            region=region,
            rule_versions=tuple(sorted({v.version for v in versions})),
        )


def default_rulebook():
    """内置一版全国基线规则，保证系统开箱可判定。"""
    book = RuleBook()
    book.add_version(RuleVersion(
        version="NR-2026-1",
        effective_from="2026-01-01",
        effective_to=None,
        region="全国",
        practitioner_requirements={
            "音声疗愈师": ("疗愈服务从业备案",),
            "水晶疗愈师": ("疗愈服务从业备案",),
            "禅修导师": ("禅修导师资质",),
            "心理咨询师": ("心理咨询师资质",),
        },
        service_requirements={
            "能量课程": ("疗愈服务备案",),
            "旅修": ("疗愈服务备案", "旅修安全预案"),
            "心理疏导": ("心理咨询机构备案",),
        },
        prohibited_claims=_DISEASE_TERMS,
        prohibited_steps=_MEDICAL_STEP_TERMS,
        efficacy_claims=_EFFICACY_TERMS,
    ))
    return book
