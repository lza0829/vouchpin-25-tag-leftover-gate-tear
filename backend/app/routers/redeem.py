from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Query
from app.services import redeem_service as svc

router = APIRouter(tags=["redeem"])


@router.get("/facts")
def facts(business_day: str | None = Query(None), tag: str | None = Query(None)):
    return svc.list_facts(business_day, tag)


@router.get("/redeem/preview")
def preview(business_day: str | None = Query(None), tag: str | None = Query(None)):
    return svc.preview(business_day, tag)


@router.post("/redeem/seal")
def seal(body: dict[str, Any]):
    return svc.seal(body)


@router.get("/batches")
def batches(business_day: str | None = Query(None)):
    return svc.list_batches(business_day)
