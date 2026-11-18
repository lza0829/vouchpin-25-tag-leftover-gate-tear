from __future__ import annotations
from datetime import datetime
from typing import Any
from fastapi import HTTPException
from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import facts as fact_repo
from app.repositories import batches as batch_repo
from app.services import tag_service


def list_facts(business_day: str | None = None, tag: str | None = None) -> list[dict[str, Any]]:
    norm_tag = tag_service.normalize_tag(tag)
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        return fact_repo.list_facts(conn, day, tag=norm_tag)
    finally:
        conn.close()


def preview(business_day: str | None = None, tag: str | None = None) -> dict[str, Any]:
    """Single read model for the nail page.

    Every field is derived from the same open-fact scan and the sealed-batch
    gate; there is no separate materialized leftover table to drift away from
    either of them.
    """
    norm_tag = tag_service.normalize_tag(tag)
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        gate = tag_service.day_gate(conn, day)
        opens = fact_repo.open_facts_for_day(conn, day, tag=norm_tag)
        total = round(sum(float(f["amount"]) for f in opens), 2)
        # The leftover list is exactly "open facts not in this batch's tag".
        leftover = tag_service.leftover_view(conn, day, norm_tag)
        nailable = tag_service.can_nail(conn, day)
        return {
            "business_day": day,
            "tag": norm_tag,
            "open_count": len(opens),
            "open_total": total,
            "facts": opens,
            "gate": gate,
            "leftover_tags": leftover["leftover_tags"],
            "leftover_count": leftover["leftover_count"],
            "leftover_total": leftover["leftover_total"],
            "leftover_rows": leftover["leftover_rows"],
            # One formula for every caller: closed gate or no open => cannot nail.
            "can_nail": nailable,
            "requires_leftover_action": leftover["leftover_count"] > 0 and not gate["sealed"],
            "tag_overview": tag_service.merged_tag_overview(conn, day),
        }
    finally:
        conn.close()


def seal(body: dict[str, Any]) -> dict[str, Any]:
    tag = tag_service.normalize_tag(body.get("tag"))
    action = body.get("leftover_action")
    if action is not None and action not in {"carry", "void"}:
        raise HTTPException(400, "leftover_action 须为 carry 或 void")
    conn = connect(immediate=True)
    try:
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        # Day gate first: any sealed batch -- whatever its tag -- closes the day.
        tag_service.assert_day_open(conn, day, "再钉扎")

        opens = fact_repo.open_facts_for_day(conn, day, tag=tag)
        if not opens:
            hint = f"（tag={tag!r}）" if tag is not None else ""
            raise HTTPException(400, f"业务日 {day} 无可钉扎的 open 核销事实{hint}")

        # Leftovers are computed from the very same scan, inside the same
        # BEGIN IMMEDIATE transaction, and closed out before the gate closes.
        leftovers = tag_service.leftover_open_facts(conn, day, tag)
        if leftovers and action not in {"carry", "void"}:
            tags = sorted({str(f["tag"]) for f in leftovers})
            raise HTTPException(
                409,
                {
                    "message": "本批将关日闸，其它 tag 仍有 open 事实，需显式收口",
                    "leftover_tags": tags,
                    "leftover_count": len(leftovers),
                    "allowed_leftover_action": ["carry", "void"],
                },
            )
        closure = tag_service.close_leftovers(conn, day, leftovers, action or "none")

        total = round(sum(float(f["amount"]) for f in opens), 2)
        bid = batch_repo.create_batch(
            conn,
            business_day=day,
            status="sealed",
            fact_count=len(opens),
            total_amount=total,
            sealed_at=datetime.now().replace(microsecond=0).isoformat(),
            note=body.get("note") or "",
            tag=tag,
            leftover_tags=sorted({str(f["tag"]) for f in leftovers}),
        )
        fact_repo.mark_batched(conn, [int(f["id"]) for f in opens], bid)

        # Re-read through the single source of truth and assert the
        # transaction cannot commit in a torn state: a closed gate with open
        # facts still behind it is refused.
        remaining = fact_repo.open_facts_for_day(conn, day)
        if remaining:
            raise HTTPException(
                409,
                f"收口后仍有 {len(remaining)} 笔 open 事实，拒绝在遗留未清时关闸",
            )

        conn.commit()
        return {
            "batch_id": bid,
            "business_day": day,
            "tag": tag,
            "fact_count": len(opens),
            "total_amount": total,
            "status": "sealed",
            "day_closed": True,
            "leftover_tags": sorted({str(f["tag"]) for f in leftovers}),
            "leftover_closure": closure,
        }
    finally:
        conn.close()


def list_batches(business_day: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_batches(conn, business_day)
    finally:
        conn.close()
