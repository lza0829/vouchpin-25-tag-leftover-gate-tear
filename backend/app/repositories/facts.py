from __future__ import annotations
import sqlite3
from typing import Any


def _filters(business_day: str | None, tag: str | None, status: str | None) -> tuple[str, list[Any]]:
    where: list[str] = []
    args: list[Any] = []
    if business_day:
        where.append("business_day=?")
        args.append(business_day)
    if tag is not None:
        where.append("tag=?")
        args.append(tag)
    if status:
        where.append("status=?")
        args.append(status)
    return (" WHERE " + " AND ".join(where)) if where else "", args


def list_facts(
    conn: sqlite3.Connection,
    business_day: str | None = None,
    tag: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    clause, args = _filters(business_day, tag, status)
    order = " ORDER BY id" if business_day else " ORDER BY id DESC LIMIT 200"
    rows = conn.execute(f"SELECT * FROM redeem_facts{clause}{order}", args).fetchall()
    return [dict(r) for r in rows]


def insert_fact(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        "INSERT INTO redeem_facts(coupon_code, campaign_code, amount, business_day, event_id, status, tag) VALUES(?,?,?,?,?,?,?)",
        (
            fields["coupon_code"],
            fields["campaign_code"],
            float(fields["amount"]),
            fields["business_day"],
            int(fields["event_id"]),
            fields.get("status") or "open",
            fields.get("tag") or "",
        ),
    )
    return int(cur.lastrowid)


def open_facts_for_day(
    conn: sqlite3.Connection,
    business_day: str,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    """Open facts of a day, optionally narrowed to one tag.

    ``tag=None`` means *no tag filter* (all open facts); the empty string is a
    real tag value and is matched literally.
    """
    return list_facts(conn, business_day, tag=tag, status="open")


def mark_batched(conn: sqlite3.Connection, fact_ids: list[int], batch_id: int) -> None:
    for fid in fact_ids:
        conn.execute(
            "UPDATE redeem_facts SET status='sealed', batch_id=? WHERE id=?",
            (batch_id, fid),
        )


def carry_to_day(conn: sqlite3.Connection, fact_id: int, target_day: str) -> None:
    """Re-home an open fact onto another business day.

    The fact keeps its original tag and stays ``open`` so it can join a batch
    sealed on the target day.  ``carried_from_day`` preserves provenance.
    """
    conn.execute(
        "UPDATE redeem_facts SET business_day=?, carried_from_day=COALESCE(carried_from_day, business_day) "
        "WHERE id=? AND status='open'",
        (target_day, fact_id),
    )


def void_fact(conn: sqlite3.Connection, fact_id: int, at: str) -> None:
    conn.execute(
        "UPDATE redeem_facts SET status='voided', voided_at=?, batch_id=NULL WHERE id=? AND status='open'",
        (at, fact_id),
    )


def leftover_facts(conn: sqlite3.Connection, business_day: str, excluded_tag: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM redeem_facts WHERE business_day=? AND status='open' AND tag<>? ORDER BY id",
        (business_day, excluded_tag),
    ).fetchall()
    return [dict(r) for r in rows]


def day_is_sealed(conn: sqlite3.Connection, business_day: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM redeem_batches WHERE business_day=? AND status='sealed' LIMIT 1",
        (business_day,),
    ).fetchone()
    return row is not None


def sealed_tags_for_day(conn: sqlite3.Connection, business_day: str) -> list[str]:
    rows = conn.execute(
        "SELECT tag FROM redeem_batches WHERE business_day=? AND status='sealed' ORDER BY id",
        (business_day,),
    ).fetchall()
    return [str(r["tag"]) for r in rows if r["tag"] is not None]


def tag_totals(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    """Per-tag facts/amount roll-up for one business day."""
    rows = conn.execute(
        "SELECT tag, status, COUNT(*) fact_count, COALESCE(SUM(amount), 0) total_amount "
        "FROM redeem_facts WHERE business_day=? GROUP BY tag, status ORDER BY tag, status",
        (business_day,),
    ).fetchall()
    return [dict(r) for r in rows]
