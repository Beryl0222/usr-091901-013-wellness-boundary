"""准入规则与负面清单：按地区与时间生效，内容版本化，评估时留痕。"""

from .util import DomainError, in_window, now_iso

NEGATIVE_KINDS = ("宣传用语", "服务步骤", "预付捆绑")
SEVERITIES = ("越界", "人工")


def _next_version(store, table, rule_key):
    return len([r for r in store.all(table) if r["rule_key"] == rule_key]) + 1


def add_admission_rule(store, *, rule_key, region, categories, required_credentials,
                       effective_from, effective_to=None, note=""):
    """登记一条准入规则；同一 rule_key 重复登记形成递增版本。"""
    rule = {
        "id": store.next_id("rule"),
        "rule_key": rule_key,
        "version": _next_version(store, "admission_rules", rule_key),
        "region": region,
        "categories": list(categories),
        "required_credentials": list(required_credentials),
        "effective_from": effective_from,
        "effective_to": effective_to,
        "note": note,
        "created_at": now_iso(),
    }
    return store.insert("admission_rules", rule)


def add_negative_entry(store, *, rule_key, region, kind, pattern, severity,
                       categories=None, effective_from, effective_to=None, note=""):
    """登记一条负面清单；severity 为 越界（直接越界）或 人工（转人工审查）。

    pattern 的匹配方式随 kind 而定：宣传用语与服务步骤按文本包含匹配，
    预付捆绑的 pattern 为预付金额上限（元）。
    """
    if kind not in NEGATIVE_KINDS:
        raise DomainError(f"未知负面清单类别：{kind}")
    if severity not in SEVERITIES:
        raise DomainError(f"未知严重度：{severity}")
    entry = {
        "id": store.next_id("neg"),
        "rule_key": rule_key,
        "version": _next_version(store, "negative_entries", rule_key),
        "region": region,
        "categories": list(categories) if categories else None,
        "kind": kind,
        "pattern": pattern,
        "severity": severity,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "note": note,
        "created_at": now_iso(),
    }
    return store.insert("negative_entries", entry)


def _region_matches(rule_region, service_region):
    """规则地区为 *、与服务地区相同或为其上级时生效。"""
    if rule_region in ("*", service_region):
        return True
    return service_region.startswith(rule_region.rstrip("/") + "/")


def _applicable(record, region, category, at):
    if not _region_matches(record["region"], region):
        return False
    categories = record.get("categories")
    if categories and category not in categories:
        return False
    return in_window(at, record["effective_from"], record["effective_to"])


def active_admission_rules(store, region, category, at):
    """某地区某服务类别在指定时间生效的准入规则。"""
    return [r for r in store.all("admission_rules") if _applicable(r, region, category, at)]


def active_negative_entries(store, region, category, at):
    """某地区某服务类别在指定时间生效的负面清单条目。"""
    return [e for e in store.all("negative_entries") if _applicable(e, region, category, at)]
