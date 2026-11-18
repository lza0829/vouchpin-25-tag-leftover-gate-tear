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

## 单真相管道（tag 子集撕裂事故的收口）

历史上存在过一条分叉管道：独立的 `tag_index` / `leftover_material` 物化表和
`gate_force_closed:*` 设置位。它与 `redeem_facts` 的 open 扫描各写各的，造成清单
多码/漏码、改 tag 后清单仍旧、闸已关却显示可钉、刷新中误关闸等撕裂。该管道已整体
删除，启动时 `db.init_db()` 会清理旧库中残留的表与设置位。

现在全系统只有一个真相来源——**现行事件 tag + `redeem_facts` 的 open 扫描**：

- 事件改 tag（`POST /events/{id}/tag`）在同一事务内同步其 open 核销事实的 tag 分区；
  已 sealed/voided 的事实属于关闸日，改 tag 返回 409；
- leftover 清单（券码/金额/tag）是 open 扫描的纯派生读模型，恒等于「非本批 tag 的
  剩余 open」，不存在独立物化表，因此不可能与扫描不一致；
- 日闸态只由当日 sealed 批推导；可钉性 `can_nail = 闸未关 且 存在 open`，钉扎页、
  总览、写路径共用同一公式，杜绝「显示可钉却写入 409」及其反向矛盾；
- 钉扎在一个 `BEGIN IMMEDIATE` 事务内完成 open 扫描 → 遗留 carry/void 收口 → 建批 →
  事实置 sealed，提交前再次扫描确认无 open 残留，否则拒绝关闸；
- 不再有独立的「刷新 leftover」接口（旧 `POST /tags/refresh` 已移除）：读模型每次
  请求即按当前扫描重算，钉与刷新交错时只会看到「未关且可钉」或「已关且不可钉」两种
  一致状态。


验证：

```
cd backend
python -m unittest discover -s tests -v
```

测试覆盖用户要求的人工核对路径：先只钉一个 tag，再尝试核销另一个 tag、钉另一个 tag、
以及重复钉同一 tag，三种操作都应被日闸拒绝；另含遗留 carry/void 与按 tag 汇总的断言。
