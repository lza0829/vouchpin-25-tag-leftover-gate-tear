from __future__ import annotations
import json
import sqlite3
from typing import Any


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["leftover_tags"] = json.loads(d.get("leftover_tags") or "[]")
    return d


def list_batches(conn: sqlite3.Connection, business_day: str | None = None) -> list[dict[str, Any]]:
    if business_day:
        rows = conn.execute(
            "SELECT * FROM redeem_batches WHERE business_day=? ORDER BY id DESC",
            (business_day,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM redeem_batches ORDER BY id DESC").fetchall()
    return [_row(r) for r in rows]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM redeem_batches WHERE id=?", (batch_id,)).fetchone()
    return _row(row) if row else None


def first_sealed_batch(conn: sqlite3.Connection, business_day: str) -> dict[str, Any] | None:
    """The batch that closed the day gate (earliest sealed batch)."""
    row = conn.execute(
        "SELECT * FROM redeem_batches WHERE business_day=? AND status='sealed' ORDER BY id LIMIT 1",
        (business_day,),
    ).fetchone()
    return _row(row) if row else None


def create_batch(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        "INSERT INTO redeem_batches(business_day, status, fact_count, total_amount, sealed_at, note, tag, leftover_tags) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            fields["business_day"],
            fields.get("status") or "sealed",
            int(fields.get("fact_count") or 0),
            float(fields.get("total_amount") or 0),
            fields.get("sealed_at"),
            fields.get("note") or "",
            fields.get("tag"),
            json.dumps(sorted(fields.get("leftover_tags") or []), ensure_ascii=False),
        ),
    )
    return int(cur.lastrowid)
