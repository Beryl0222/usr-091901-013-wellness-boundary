# 疗愈服务边界治理

管理疗愈经营主体、服务准入、宣传用语、预付合同和投诉处置。项目以领域契约约定参与者、状态和不可破坏的业务原则，各模块围绕同一语义协作。

## 领域模块

- `wellness/declaration.py` — 经营者申报：主体、从业人员、服务步骤、价格与宣传用语
- `wellness/rules.py` — 准入规则与负面清单，按地区和时间生效，同一规则键形成递增版本
- `wellness/evaluation.py` — 准入评估：允许发布 / 补充材料 / 待人工审查 / 越界；疾病治疗类宣传一律转人工审查，人工结论在后续评估中保持效力
- `wellness/responsibility.py` — 渠道平台、酒店、实际提供者分别确认责任，三方齐备后服务才可在售
- `wellness/contracts.py` — 预付合同的拆分、转赠、取消与未消费退款，历次变更保留原合同版本，退款单记录对应合同版本
- `wellness/cases.py` — 巡查记录、消费者证据、商家申辩分区隔离；重复投诉仅作关联，不能据此直接定性
- `wellness/oversight.py` — 监管解释：某条宣传当时为何被限制、处置影响哪些在售服务、退款执行到哪一步、同一主体换名跨区线索
- `wellness/store.py` — 进程内记录仓储与快照

## 运行

- `python3 service.py --check` 核对服务配置与领域契约
- `python3 service.py --port 8000 [--data snapshot.json]` 启动接口服务，可选快照文件跨重启保留数据
- `python3 -m unittest -v` 运行全部测试

## 接口摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` `/contract` | 健康检查与领域契约 |
| POST | `/subjects` `/practitioners` `/services` | 经营者申报 |
| POST | `/rules/admission` `/rules/negative` | 维护准入规则与负面清单 |
| POST | `/services/{id}/evaluate` | 准入评估，可用 `at` 指定时间 |
| POST | `/claims/{id}/review` | 宣传用语人工审查 |
| POST | `/responsibility/{service_id}/confirm` | 三方责任确认 |
| POST | `/contracts` 及 `/contracts/{id}/consume` `split` `transfer` `cancel` `refund` | 预付合同流转 |
| GET | `/refunds/{id}` | 退款执行进度 |
| POST | `/cases` 及 `/cases/{id}/materials` `links` `close` | 案件流转，材料按角色分区 |
| GET | `/cases/{id}?role=...` | 按角色读取案件 |
| GET | `/oversight/claims/{id}/explanation?at=...` | 某条宣传当时为何被限制 |
| GET | `/oversight/dispositions/{id}/impact` | 处置影响的在售服务 |
| GET | `/oversight/renamed-subjects` | 换名跨区经营线索 |
