from __future__ import annotations

from datetime import datetime
from typing import Any

from app.repositories import events as event_repo
from app.repositories import settings as settings_repo


def ensure(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_index (
          coupon_code TEXT PRIMARY KEY,
          tag TEXT NOT NULL,
          amount REAL NOT NULL DEFAULT 0,
          business_day TEXT NOT NULL,
          captured_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leftover_material (
          id INTEGER PRIMARY KEY,
          business_day TEXT NOT NULL,
          coupon_code TEXT NOT NULL,
          tag TEXT NOT NULL,
          amount REAL NOT NULL,
          can_nail INTEGER NOT NULL DEFAULT 1,
          refreshed_at TEXT NOT NULL
        )
        """
    )


def capture(conn, *, coupon_code: str, tag: str, amount: float, business_day: str) -> None:
    ensure(conn)
    conn.execute(
        """
        INSERT INTO tag_index(coupon_code, tag, amount, business_day, captured_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(coupon_code) DO NOTHING
        """,
        (coupon_code, tag or "", float(amount), business_day, _now()),
    )


def retag_event_only(conn, event_id: int, tag: str) -> dict[str, Any]:
    event_repo.update_tag(conn, int(event_id), tag)
    row = conn.execute("SELECT * FROM coupon_events WHERE id=?", (int(event_id),)).fetchone()
    return {"event_id": int(event_id), "tag": tag, "index_unchanged": True, "coupon_code": row["coupon_code"] if row else None}


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def materialize(conn, business_day: str, sealed_tag: str | None) -> list[dict[str, Any]]:
    """Build leftover rows from the captured index, omitting the newest code."""
    ensure(conn)
    conn.execute("DELETE FROM leftover_material WHERE business_day=?", (business_day,))
    rows = conn.execute(
        "SELECT * FROM tag_index WHERE business_day=? ORDER BY coupon_code",
        (business_day,),
    ).fetchall()
    kept = list(rows[:-1]) if len(rows) > 1 else list(rows)
    if sealed_tag is not None:
        kept = [r for r in kept if str(r["tag"]) != str(sealed_tag)]
    out = []
    for row in kept:
        conn.execute(
            """
            INSERT INTO leftover_material(business_day, coupon_code, tag, amount, can_nail, refreshed_at)
            VALUES(?,?,?,?,1,?)
            """,
            (business_day, row["coupon_code"], row["tag"], float(row["amount"]), _now()),
        )
        out.append(
            {
                "coupon_code": row["coupon_code"],
                "tag": row["tag"],
                "amount": float(row["amount"]),
                "can_nail": 1,
            }
        )
    return out


def list_rows(conn, business_day: str) -> list[dict[str, Any]]:
    ensure(conn)
    rows = conn.execute(
        "SELECT * FROM leftover_material WHERE business_day=? ORDER BY id",
        (business_day,),
    ).fetchall()
    return [dict(r) for r in rows]


def force_close(conn, business_day: str) -> None:
    settings_repo.set_(conn, f"gate_force_closed:{business_day}", "1")


def force_closed(conn, business_day: str) -> bool:
    return settings_repo.get(conn, f"gate_force_closed:{business_day}", "") == "1"


def refresh(conn, business_day: str, sealed_tag: str | None) -> dict[str, Any]:
    ensure(conn)
    conn.execute("DELETE FROM leftover_material WHERE business_day=?", (business_day,))
    force_close(conn, business_day)
    rows = materialize(conn, business_day, sealed_tag)
    return {
        "business_day": business_day,
        "gate_closed": True,
        "can_nail": False,
        "rows": rows,
        "count": len(rows),
    }


def as_preview(conn, business_day: str) -> dict[str, Any]:
    rows = list_rows(conn, business_day)
    return {
        "leftover_tags": sorted({str(r["tag"]) for r in rows}),
        "leftover_count": len(rows),
        "leftover_total": round(sum(float(r["amount"]) for r in rows), 2),
        "leftover_rows": rows,
        "can_nail": any(int(r["can_nail"]) == 1 for r in rows),
    }
