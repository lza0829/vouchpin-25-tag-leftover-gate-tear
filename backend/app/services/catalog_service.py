from __future__ import annotations
from typing import Any
from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import facts as fact_repo
from app.repositories import batches as batch_repo
from app.repositories import events as event_repo
from app.services import tag_service


def dashboard() -> dict[str, Any]:
    conn = connect()
    try:
        day = settings_repo.get(conn, "business_day")
        facts = fact_repo.list_facts(conn, day)
        open_n = sum(1 for f in facts if f["status"] == "open")
        sealed_n = sum(1 for f in facts if f["status"] == "sealed")
        voided_n = sum(1 for f in facts if f["status"] == "voided")
        # Voided facts are excluded from the money total: they no longer count.
        total = round(sum(float(f["amount"]) for f in facts if f["status"] != "voided"), 2)
        events = event_repo.list_events(conn, day)
        batches = batch_repo.list_batches(conn)
        gate = tag_service.day_gate(conn, day)
        return {
            "business_day": day,
            "event_count": len(events),
            "fact_count": len(facts),
            "open_fact_count": open_n,
            "sealed_fact_count": sealed_n,
            "voided_fact_count": voided_n,
            "redeem_total_today": total,
            "batch_count": len(batches),
            "day_sealed": gate["sealed"],
            "gate_tag": gate["tag"],
            "gate_batch_id": gate["batch_id"],
            "leftover_tags": gate["leftover_tags"],
            "tag_overview": tag_service.merged_tag_overview(conn, day),
            "leftover_posted": __import__('app.services.tag_material', fromlist=['as_preview']).as_preview(conn, day),
        }
    finally:
        conn.close()
