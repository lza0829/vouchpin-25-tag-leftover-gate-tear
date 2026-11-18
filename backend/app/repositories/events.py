from __future__ import annotations
import sqlite3
from typing import Any


def list_events(
    conn: sqlite3.Connection,
    business_day: str | None = None,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    if business_day and tag is not None:
        rows = conn.execute(
            "SELECT * FROM coupon_events WHERE business_day=? AND tag=? ORDER BY id",
            (business_day, tag),
        ).fetchall()
    elif business_day:
        rows = conn.execute(
            "SELECT * FROM coupon_events WHERE business_day=? ORDER BY id",
            (business_day,),
        ).fetchall()
    elif tag is not None:
        rows = conn.execute(
            "SELECT * FROM coupon_events WHERE tag=? ORDER BY id DESC LIMIT 200",
            (tag,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM coupon_events ORDER BY id DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


def insert_event(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        "INSERT INTO coupon_events(event_type, coupon_code, campaign_code, amount, business_day, occurred_at, note, tag) VALUES(?,?,?,?,?,?,?,?)",
        (
            fields["event_type"],
            fields["coupon_code"],
            fields["campaign_code"],
            float(fields.get("amount") or 0),
            fields["business_day"],
            fields["occurred_at"],
            fields.get("note") or "",
            fields.get("tag") or "",
        ),
    )
    return int(cur.lastrowid)


def update_tag(conn: sqlite3.Connection, event_id: int, tag: str) -> None:
    conn.execute("UPDATE coupon_events SET tag=? WHERE id=?", (tag, int(event_id)))


def distinct_tags(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT tag FROM coupon_events WHERE tag<>'' ORDER BY tag"
    ).fetchall()
    return [str(r["tag"]) for r in rows]
