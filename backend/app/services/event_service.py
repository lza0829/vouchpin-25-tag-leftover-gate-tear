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
        if et == "redeem":
            from app.services import tag_material
            tag_material.capture(
                conn,
                coupon_code=code,
                tag=tag,
                amount=amount,
                business_day=day,
            )
        conn.commit()
        return {"event_id": eid, "fact_id": fact_id, "business_day": day, "tag": tag}
    finally:
        conn.close()


def retag(event_id: int, body: dict[str, Any]) -> dict[str, Any]:
    tag = tag_service.normalize_tag(body.get("tag")) or ""
    conn = connect(immediate=True)
    try:
        from app.services import tag_material
        result = tag_material.retag_event_only(conn, event_id, tag)
        conn.commit()
        return result
    finally:
        conn.close()


def refresh_leftover(body: dict[str, Any]) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        from app.services import tag_material
        result = tag_material.refresh(conn, day, body.get("tag"))
        conn.commit()
        return result
    finally:
        conn.close()
