"""Single source of truth for tag-subset sealing.

The chosen (global) policy is ``config.TAG_SEAL_POLICY == "subset_closes_day"``:

* a seal request may name one tag and only pulls that tag's ``open`` facts
  into the sealed batch (the batch snapshots ``tag``);
* the very first sealed batch of a business day closes that day's write gate,
  regardless of its tag -- no further redeem fact may be written and no other
  tag may be sealed;
* any other open tag left outside the batch is recorded on the batch as
  ``leftover_tags`` and must be closed out in the same transaction (carry it
  to the next unsealed day or explicitly void it); leaving it open behind a
  closed gate is refused;
* the dashboard roll-up groups the day by tag.

The alternative reading ("subset keeps the day open") is intentionally not
implemented; see ``config.TAG_SEAL_POLICY``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from app import config
from app.db import connect
from app.repositories import batches as batch_repo
from app.repositories import facts as fact_repo

MAX_TAG_LEN = 32

if config.TAG_SEAL_POLICY != "subset_closes_day":  # pragma: no cover - guard
    raise RuntimeError(
        f"unsupported TAG_SEAL_POLICY={config.TAG_SEAL_POLICY!r}; "
        "only 'subset_closes_day' is implemented"
    )


def normalize_tag(value: Any) -> str | None:
    """Return a stripped tag, or ``None`` when no tag filter was supplied.

    The empty string is a legitimate tag value (the "untagged" bucket); it is
    distinct from ``None``, which means "do not filter by tag".
    """
    if value is None:
        return None
    tag = str(value).strip()
    if len(tag) > MAX_TAG_LEN:
        raise HTTPException(400, f"tag 长度不得超过 {MAX_TAG_LEN}")
    if any(ord(ch) < 32 for ch in tag):
        raise HTTPException(400, "tag 不得包含控制字符")
    return tag


def day_gate(conn, business_day: str) -> dict[str, Any]:
    """Describe the day-level write gate for one business day."""
    batch = batch_repo.first_sealed_batch(conn, business_day)
    return {
        "business_day": business_day,
        "sealed": batch is not None,
        "tag": (batch or {}).get("tag"),
        "batch_id": (batch or {}).get("id"),
        "leftover_tags": (batch or {}).get("leftover_tags") or [],
    }


def assert_day_open(conn, business_day: str, what: str) -> None:
    from app.services import tag_material
    if tag_material.force_closed(conn, business_day):
        raise HTTPException(409, f"业务日 {business_day} 写闸已关，禁止{what}")
    gate = day_gate(conn, business_day)
    if gate["sealed"]:
        detail = f"业务日 {business_day} 已由 sealed 批 #{gate['batch_id']}"
        if gate["tag"] is not None:
            detail += f"（tag={gate['tag']!r}）"
        detail += f"关闸，禁止{what}"
        raise HTTPException(409, detail)


def next_unsealed_day(conn, business_day: str) -> str:
    """First calendar day strictly after ``business_day`` with no sealed batch."""
    try:
        cursor = date.fromisoformat(business_day)
    except ValueError as exc:
        raise HTTPException(400, f"business_day 非法: {business_day!r}") from exc
    for _ in range(3660):
        cursor = cursor + timedelta(days=1)
        candidate = cursor.isoformat()
        if not fact_repo.day_is_sealed(conn, candidate):
            return candidate
    raise HTTPException(409, "找不到可用于改挂的未钉扎业务日")


def leftover_open_facts(conn, business_day: str, tag: str | None) -> list[dict[str, Any]]:
    """Open facts of the day that a ``tag``-subset seal would leave behind."""
    if tag is None:
        return []
    return fact_repo.leftover_facts(conn, business_day, tag)


def close_leftovers(
    conn,
    business_day: str,
    facts: list[dict[str, Any]],
    action: str,
    target_day: str | None = None,
) -> dict[str, Any]:
    """Carry leftover facts to the next unsealed day, or void them.

    Must run in the same transaction as the seal so the gate never closes
    while known leftovers stay indefinitely ``open``.
    """
    if not facts:
        return {"action": action, "count": 0, "target_day": None, "fact_ids": []}
    ids = [int(f["id"]) for f in facts]
    if action == "void":
        at = datetime.now().replace(microsecond=0).isoformat()
        for fid in ids:
            fact_repo.void_fact(conn, fid, at)
        return {"action": "void", "count": len(ids), "target_day": None, "fact_ids": ids}
    if action == "carry":
        if target_day is None:
            target = next_unsealed_day(conn, business_day)
        else:
            try:
                target = date.fromisoformat(str(target_day)).isoformat()
            except ValueError as exc:
                raise HTTPException(400, f"leftover_target_day 非法: {target_day!r}") from exc
        if target == business_day or fact_repo.day_is_sealed(conn, target):
            raise HTTPException(409, f"改挂目标日 {target} 不可用")
        for fid in ids:
            fact_repo.carry_to_day(conn, fid, target)
        return {"action": "carry", "count": len(ids), "target_day": target, "fact_ids": ids}
    raise HTTPException(400, "leftover_action 须为 carry 或 void")


def tag_overview(conn, business_day: str) -> list[dict[str, Any]]:
    """Per-tag roll-up for one business day, including the empty (untagged) tag."""
    buckets: dict[str, dict[str, Any]] = {}
    for row in fact_repo.tag_totals(conn, business_day):
        tag = str(row["tag"])
        b = buckets.setdefault(
            tag,
            {
                "tag": tag,
                "fact_count": 0,
                "open_count": 0,
                "sealed_count": 0,
                "voided_count": 0,
                "total_amount": 0.0,
                "open_amount": 0.0,
                "sealed_amount": 0.0,
                "voided_amount": 0.0,
            },
        )
        status = str(row["status"])
        n = int(row["fact_count"])
        amt = float(row["total_amount"])
        b["fact_count"] += n
        b["total_amount"] = round(b["total_amount"] + amt, 2)
        if status in {"open", "sealed", "voided"}:
            b[f"{status}_count"] += n
            b[f"{status}_amount"] = round(b[f"{status}_amount"] + amt, 2)
        else:
            b[f"{status}_count"] = b.get(f"{status}_count", 0) + n
    return [buckets[k] for k in sorted(buckets)]


def merged_tag_overview(conn, business_day: str) -> list[dict[str, Any]]:
    """Roll-up plus the tags actually sealed today, in one shape for the UI."""
    sealed = set(fact_repo.sealed_tags_for_day(conn, business_day))
    rows = tag_overview(conn, business_day)
    seen = {r["tag"] for r in rows}
    for tag in sealed - seen:
        rows.append(
            {
                "tag": tag,
                "fact_count": 0,
                "open_count": 0,
                "sealed_count": 0,
                "voided_count": 0,
                "total_amount": 0.0,
                "open_amount": 0.0,
                "sealed_amount": 0.0,
                "voided_amount": 0.0,
            }
        )
    for r in rows:
        r["sealed_today"] = r["tag"] in sealed
    return sorted(rows, key=lambda r: r["tag"])
