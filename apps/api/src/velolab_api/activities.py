"""Owned cached recent activity read; no provider I/O or raw payload responses."""

import math
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from velolab_api.auth import SessionDependency
from velolab_api.integrations import PreviewOwner
from velolab_api.intervals_upsert import DEFAULT_UPSERT_POLICY
from velolab_api.models import Activity, UserIntegration
from velolab_api.preview_sync import preview_status

router = APIRouter(tags=["activities"])
ACTIVITY_PAGE_SIZE = 50


class ActivityItem(BaseModel):
    id: UUID
    name: str | None
    type: str | None
    start_local: datetime | None = Field(description="Provider-local start, no timezone conversion")
    duration_s: int | None = Field(description="Provider moving_time in seconds, not elapsed time")
    distance_m: float | None = Field(description="Provider icu_distance in meters, no fallback")
    training_load: float | None = Field(
        description="Provider icu_training_load, not computed locally"
    )


class ActivitiesResult(BaseModel):
    items: list[ActivityItem]
    has_more: bool
    coverage: Literal["recent_preview"] = "recent_preview"
    last_preview_at: datetime | None = None
    possibly_truncated: bool = False
    last_error_code: str | None = None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except OverflowError:
            pass
    return None


@router.get("/activities", response_model=ActivitiesResult)
def get_activities(user: PreviewOwner, session: SessionDependency) -> ActivitiesResult:
    user_id = user.id
    session.rollback()  # End auth's read transaction before choosing our read snapshot.
    try:
        # A sync may commit between the list and status SELECTs. Read both from
        # one snapshot so old rows cannot be paired with a newly fresh marker.
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        integration_id = session.scalar(
            select(UserIntegration.id).where(
                UserIntegration.user_id == user_id, UserIntegration.provider == "intervals"
            )
        )
        if integration_id is None:
            return ActivitiesResult(items=[], has_more=False)
        rows = session.scalars(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.integration_id == integration_id,
            )
            .order_by(Activity.start_date_local.desc().nulls_last(), Activity.id.asc())
            .limit(ACTIVITY_PAGE_SIZE + 1)
        ).all()
        fields = DEFAULT_UPSERT_POLICY.fields
        items = []
        for row in rows[:ACTIVITY_PAGE_SIZE]:
            duration = row.payload.get(fields.activity_duration)
            items.append(
                ActivityItem(
                    id=row.id,
                    name=_string(row.payload.get(fields.activity_name)),
                    type=_string(row.payload.get(fields.activity_type)),
                    start_local=row.start_date_local,
                    duration_s=duration if type(duration) is int else None,
                    distance_m=_number(row.payload.get(fields.activity_distance)),
                    training_load=_number(row.training_load),
                )
            )
        state = preview_status(session, user_id, integration_id)
        return ActivitiesResult(
            items=items,
            has_more=len(rows) > ACTIVITY_PAGE_SIZE,
            last_preview_at=state.last_preview_at,
            possibly_truncated=state.possibly_truncated,
            last_error_code=state.last_error_code,
        )
    except SQLAlchemyError:
        session.rollback()
    raise HTTPException(503, "persistence_failure")
