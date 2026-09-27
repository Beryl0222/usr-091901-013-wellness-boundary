# 疗愈服务边界治理

面向音声、水晶、禅修旅修与线上能量课程进入酒店/文旅渠道后的治理后端。经营者申报主体、从业人员、服务步骤、价格与宣传用语；系统依据**随地区与时间生效**的准入规则与负面清单标出「需补证 / 越界 / 可发布」状态，涉及疾病治疗的主张一律转人工审查。线上平台、酒店、实际提供者分别确认责任；套餐拆分、转赠、取消与未消费退款都锁定原合同版本；巡查、消费者证据与商家申辩进入同一案件但字段隔离；重复投诉只关联不定罪。

## 模块

| 文件 | 职责 |
|---|---|
| `rules.py` | 规则版本（地区/生效时段）、资质准入、宣传用语与服务步骤判定，命中即记录规则版本快照 |
| `governance.py` | 核心领域：主体/人员/服务申报、人工审查、三方责任、合同版本、套餐生命周期、案件证据、改名跨区发现、追溯分析 |
| `service.py` | HTTP/JSON 接口与角色约束；保留 `/health`、`/contract` |
| `domain_contract.json` | 领域契约：参与者、状态、四类判定结论、不可破坏的不变量 |

## 运行

```bash
python3 service.py --check          # 配置与规则基线自检
python3 service.py --port 8000      # 启动服务
python3 -m unittest -v              # 39 个测试（领域 31 + HTTP 8）
```

## 主要接口

调用身份在 POST 报文中以 `role`/`actor` 提供（GET 用同名查询参数）。

### 申报与判定
- `POST /subjects`、`POST /practitioners`、`POST /services`（可带 `claims` 宣传用语）
- `POST /services/{id}/claims` 追加宣传；`POST /services/{id}/evaluate` 按现行规则重判
- `POST /services/{id}/sale` 上架/下架——非「允许发布」状态禁止上架
- `GET /rules?region=杭州` 查询当时生效规则；`POST /rules/versions`（监管角色）发布地区/时效新版本

宣传判定返回四态：**可发布 / 需补证 / 转人工审查 / 越界**。命中疾病治疗主张时服务自动进入「限制经营」并入人工审查队列。

### 人工审查（案件审核员）
- `POST /reviews/take` 领取、`POST /reviews/{claim_id}/resolve` 作出结论
- `GET /claims/{id}/explain` 还原该宣传**当时**为何被限制：判定日、地区、生效规则版本、每条命中理由
- 系统不替人判断：审查通过解除限制但快照保留；维持限制则继续下架

### 三方责任与合同套餐
- `POST /services/{id}/responsibility`：`线上平台`（渠道平台角色）、`酒店`、`实际提供者`（经营者角色）**分别**确认，不能代签；`GET` 查询确认状态
- 三方未全部确认时 `POST /orders` 销售预付套餐被拒绝
- `POST /contracts`、`POST /contracts/{id}/amend`（旧版本永久保留）
- `POST /orders/{id}/consume|split|transfer|cancel|refund`；拆分/转赠/退款全部记录原合同版本
- `POST /orders/{id}/refunds/{rid}/advance` 推进「已申请→已核定→执行中→已退款」
- `GET /orders/{id}/refunds` 查询退款执行到哪一步与完整事件链

### 案件与追溯
- `POST /complaints`、`POST /cases`（监管角色立案）、`POST /cases/{id}/complaints` 关联重复投诉
- `POST /cases/{id}/evidence`：`巡查证据`/`消费者证据`/`商家申辩` 三桶，提交角色受限；`GET` 按角色隔离可见字段（仅审核员可见全部）
- `POST /cases/{id}/ruling` **仅案件审核员**可作违法违规认定——投诉数量永不自动定罪
- `POST /cases/{id}/restrict` 下架处置并返回影响范围（同主体同类型、共用宣传用语的在售服务与相关订单）
- `GET /services/{id}/impact` 处置影响分析
- `GET /subjects/{id}/aliases`（监管角色）：按信用代码、共用电话、名称近似、共用从业人员发现换名跨区疑似主体，只给线索不自动等同

## 关键不变量

见 `domain_contract.json`，核心三条：

1. 疾病治疗相关宣传必须进入人工审查；
2. 投诉关联不等同于违法认定；
3. 退款和经营处置引用原合同及规则版本（时间旅行可追溯）。
