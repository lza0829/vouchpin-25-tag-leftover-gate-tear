# Vouchpin（券钉簿）

骨架：券事件入湖 → 核销事实钉扎 → 过期/退回窗（后挖）→ 批次互证。

本地：
- API: `cd backend && python -m uvicorn app.main:app --port 10800`
- Web: `cd frontend && npm install && npm run dev`（5800）

## tag 子集钉扎（全局互斥策略）

核销事件可带 `tag`（渠道/场景标签，独立于活动 `campaign_code`）。钉扎请求可带
`tag`，只把该 tag 的 `open` 事实纳入 sealed 批，批快照记该 `tag`。

本仓库**只贯彻一种**策略（`backend/app/config.py: TAG_SEAL_POLICY`）：

**`subset_closes_day` —— 任一子集钉即关日闸**

- 一个业务日出现**任意** sealed 批（无论 tag）后，禁止再写入任何核销事实，也禁止再钉其它 tag；
- 未纳入本批的其它 tag 的 `open` 事实，会在批快照记入 `leftover_tags`；
- 关闸前必须在同一事务内显式收口这些遗留：`leftover_action` 取
  `carry`（改挂到下一个未钉日，保留 tag 与来源）或 `void`（显式作废）；
  未收口则钉扎请求返回 409，不允许“既关闸又无限期留 open”；
- 不带 `tag` 的钉扎等于整日钉扎（纳入当日全部 open 事实，无遗留）；
- 日级总览按 tag 汇总（事实 / open / sealed / voided / 金额）。

未选用的另一读法（子集钉不关日闸、其它 tag 仍可核销再钉、仅禁止同一 tag 重复钉）
不实现；`tag_service` 启动时会拒绝其它策略值，避免两套语义混用。

过滤（`GET /facts?tag=`、`GET /redeem/preview?tag=`、`GET /events?tag=`）、批快照、
日闸（`event_service` 写核销前、`redeem_service` 钉扎前）、总览与遗留收口全部经由
`backend/app/services/tag_service.py`，同一来源。

验证：

```
cd backend
python -m unittest discover -s tests -v
```

测试覆盖用户要求的人工核对路径：先只钉一个 tag，再尝试核销另一个 tag、钉另一个 tag、
以及重复钉同一 tag，三种操作都应被日闸拒绝；另含遗留 carry/void 与按 tag 汇总的断言。
