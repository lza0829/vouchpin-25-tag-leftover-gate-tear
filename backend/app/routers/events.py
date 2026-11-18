from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Query
from app.services import event_service as svc

router = APIRouter(tags=["events"])


@router.get("/campaigns")
def campaigns():
    return svc.list_campaigns()


@router.get("/tags")
def tags():
    return svc.list_tags()


@router.get("/events")
def events(business_day: str | None = Query(None), tag: str | None = Query(None)):
    return svc.list_events(business_day, tag)


@router.post("/events")
def create_event(body: dict[str, Any]):
    return svc.add_event(body)


@router.post("/events/{event_id}/tag")
def retag(event_id: int, body: dict[str, Any]):
    return svc.retag(event_id, body)


@router.post("/tags/refresh")
def refresh_tags(body: dict[str, Any]):
    return svc.refresh_leftover(body)
