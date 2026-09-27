"""进程内记录仓储：按表保存字典记录，支持整体快照与恢复。"""

from .util import NotFound


class Store:
    """各域模块共用的记录仓储。"""

    def __init__(self):
        self.tables = {}
        self.counters = {}

    def next_id(self, prefix):
        """按前缀生成递增编号，便于阅读与追踪。"""
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}-{self.counters[prefix]:04d}"

    def insert(self, table, record):
        self.tables.setdefault(table, {})[record["id"]] = record
        return record

    def get(self, table, record_id):
        try:
            return self.tables.get(table, {})[record_id]
        except KeyError:
            raise NotFound(f"{table} 中不存在 {record_id}")

    def all(self, table):
        return list(self.tables.get(table, {}).values())

    def find(self, table, **conditions):
        return [r for r in self.all(table) if all(r.get(k) == v for k, v in conditions.items())]

    def snapshot(self):
        return {"tables": {t: dict(records) for t, records in self.tables.items()},
                "counters": dict(self.counters)}

    @classmethod
    def restore(cls, snapshot):
        store = cls()
        store.tables = {t: dict(records) for t, records in snapshot["tables"].items()}
        store.counters = dict(snapshot["counters"])
        return store
