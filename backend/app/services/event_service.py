from __future__ import annotations
from datetime import datetime
from typing import Any
from fastapi import HTTPException
from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import events as event_repo
from app.repositories import facts as fact_repo
from app.repositories import campaigns as camp_repo
from app.services import tag_service


def list_campaigns() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return camp_repo.list_all(conn)
    finally:
        conn.close()


def list_events(business_day: str | None = None, tag: str | None = None) -> list[dict[str, Any]]:
    norm_tag = tag_service.normalize_tag(tag)
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        return event_repo.list_events(conn, day, tag=norm_tag)
    finally:
        conn.close()


def list_tags() -> list[str]:
    conn = connect()
    try:
        return event_repo.distinct_tags(conn)
    finally:
        conn.close()


def add_event(body: dict[str, Any]) -> dict[str, Any]:
    tag = tag_service.normalize_tag(body.get("tag")) or ""
    conn = connect(immediate=True)
    try:
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        et = body.get("event_type")
        if et not in {"issue", "redeem", "void"}:
            raise HTTPException(400, "event_type 须为 issue/redeem/void")
        code = (body.get("coupon_code") or "").strip()
        camp = (body.get("campaign_code") or "").strip()
        if not code or not camp:
            raise HTTPException(400, "coupon_code / campaign_code 必填")
        if et == "redeem":
            # Any sealed tag-subset batch closes the whole day.  The check is
            # tag-agnostic on purpose: even a different tag cannot be written.
            tag_service.assert_day_open(conn, day, "再写入核销")
        amount = float(body.get("amount") or 0)
        if et == "redeem" and amount <= 0:
            # default face value from campaign
            camps = {c["code"]: c for c in camp_repo.list_all(conn)}
            if camp in camps:
                amount = float(camps[camp]["face_value"])
        occurred = body.get("occurred_at") or datetime.now().replace(microsecond=0).isoformat()
        eid = event_repo.insert_event(
            conn,
            event_type=et,
            coupon_code=code,
            campaign_code=camp,
            amount=amount,
            business_day=day,
            occurred_at=occurred,
            note=body.get("note") or "",
            tag=tag,
        )
        fact_id = None
        if et == "redeem":
            try:
                fact_id = fact_repo.insert_fact(
                    conn,
                    coupon_code=code,
                    campaign_code=camp,
                    amount=amount,
                    business_day=day,
                    event_id=eid,
                    status="open",
                    tag=tag,
                )
            except Exception as e:
                raise HTTPException(409, f"核销事实写入失败（可能券码已核销）: {e}") from e
        conn.commit()
        return {"event_id": eid, "fact_id": fact_id, "business_day": day, "tag": tag}
    finally:
        conn.close()


def retag(event_id: int, body: dict[str, Any]) -> dict[str, Any]:
    """Change an event's tag and keep every downstream partition aligned.

    The event tag, its open redeem fact's tag, the sealing batch's leftover
    snapshot, and every read model (leftover list, gate, dashboard) must move
    together in one transaction -- that is the fix for "event tag changed but
    leftover/facts still partition by the old tag".
    """
    tag = tag_service.normalize_tag(body.get("tag")) or ""
    conn = connect(immediate=True)
    try:
        event = conn.execute(
            "SELECT * FROM coupon_events WHERE id=?", (int(event_id),)
        ).fetchone()
        if event is None:
            raise HTTPException(404, f"事件 #{event_id} 不存在")

        fact = fact_repo.get_by_event(conn, int(event_id))
        if fact is not None and fact["status"] != "open":
            # A sealed/voided fact belongs to a closed day; re-partitioning it
            # would split the batch snapshot from the facts.
            raise HTTPException(
                409,
                f"事件 #{event_id} 的核销事实已 {fact['status']}，关闸后禁止改 tag",
            )

        event_repo.update_tag(conn, int(event_id), tag)
        moved_fact_id = None
        if fact is not None:
            fact_repo.update_tag(conn, int(fact["id"]), tag)
            moved_fact_id = int(fact["id"])
            # No materialized table to refresh: the leftover list, gate view
            # and dashboard are read models of the open-fact scan, so the new
            # tag partition is visible on the very next read -- in the same
            # transaction the fact was moved in.

        conn.commit()
        return {
            "event_id": int(event_id),
            "tag": tag,
            "coupon_code": event["coupon_code"],
            "fact_id": moved_fact_id,
            "fact_retagged": moved_fact_id is not None,
        }
    finally:
        conn.close()
