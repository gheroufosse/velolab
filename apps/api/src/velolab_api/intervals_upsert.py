"""Non-destructive persistence boundary (ADR-024 slice 3), not sync orchestration.

Merge top-level provider fields atomically in PostgreSQL; nested values are
opaque field values, not JSON patches. No absent-record/deletion inference.
The caller owns commit/rollback, including any future checkpoint transaction.
Each batch has a savepoint so a failed write cannot leave part of it pending.

first_seen_at never changes. updated_at and last_seen_at advance only when
payload or projections change: identical sightings do not rewrite rows.
last_seen_at is thus the last *changed* observation, not a freshness/success
marker (that belongs to slice 4). A mapping correction can repair projections
from retained payload even when the incoming record itself is unchanged.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Numeric,
    Table,
    bindparam,
    case,
    cast,
    func,
    literal,
    literal_column,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from velolab_api.intervals_client import ActivityRecord, WellnessRecord
from velolab_api.models import Activity, UserIntegration, WellnessDay


class NullPolicy(StrEnum):
    PRESERVE = "preserve"
    CLEAR = "clear"


class UpsertError(ValueError):
    """Static validation codes only; never interpolate provider data."""


@dataclass(frozen=True, slots=True)
class PayloadFields:
    """Candidate names in ONE place; contract blocker 4, units/parity unverified.

    Athlete fields on wellness are defensive candidates, not verified provider
    facts. Override alongside the client policy when evidence changes names.
    """

    activity_utc: str = "start_date"
    activity_local: str = "start_date_local"
    activity_load: str = "icu_training_load"
    # Public OpenAPI v1.0.0 Activity schema; cached-list read projections.
    activity_name: str = "name"
    activity_type: str = "type"
    activity_duration: str = "moving_time"
    # Provider field notes recommend icu_distance, not upstream distance.
    activity_distance: str = "icu_distance"
    wellness_ctl: str = "ctl"
    wellness_atl: str = "atl"
    wellness_ramp_rate: str = "rampRate"
    wellness_resting_hr: str = "restingHR"
    wellness_weight: str = "weight"
    wellness_weight_carried_over: str = "tempWeight"
    wellness_resting_hr_carried_over: str = "tempRestingHR"
    athlete_ids: tuple[str, ...] = ("icu_athlete_id", "athlete_id")


@dataclass(frozen=True, slots=True)
class UpsertPolicy:
    # Contract blocker 3: explicit nulls cannot clear known values by default.
    explicit_nulls: NullPolicy = NullPolicy.PRESERVE
    fields: PayloadFields = field(default_factory=PayloadFields)

    def __post_init__(self) -> None:
        if not isinstance(self.explicit_nulls, NullPolicy):
            raise UpsertError("invalid_null_policy")


DEFAULT_UPSERT_POLICY = UpsertPolicy()


def _numeric_projection(payload: ColumnElement, field_name: str) -> ColumnElement:
    # JSON strings (even "42") and numbers outside float8 range are gaps.
    # The inner CASE guards the numeric cast for non-number JSON values.
    value = payload.op("->", return_type=JSONB)(field_name)
    in_range = case(
        (
            func.jsonb_typeof(value) == "number",
            func.abs(cast(payload[field_name].as_string(), Numeric))
            <= Decimal("1.7976931348623157e308"),
        ),
        else_=False,
    )
    return case((in_range, payload[field_name].as_float()), else_=None)


def _projections(
    payload: ColumnElement, model: type[Activity] | type[WellnessDay], fields: PayloadFields
) -> dict[str, ColumnElement]:
    """Single mapping, used for both incoming and merged payloads (blocker 4).

    Non-numeric/out-of-range JSON projects to a gap; malformed dates/flags still fail.
    Provider-local start is stored without conversion; UTC instant is separate.
    """
    if model is Activity:
        return {
            "start_date_utc": payload[fields.activity_utc]
            .as_string()
            .cast(DateTime(timezone=True)),
            "start_date_local": payload[fields.activity_local]
            .as_string()
            .cast(DateTime(timezone=False)),
            "training_load": _numeric_projection(payload, fields.activity_load),
        }
    return {
        "ctl": _numeric_projection(payload, fields.wellness_ctl),
        "atl": _numeric_projection(payload, fields.wellness_atl),
        "ramp_rate": _numeric_projection(payload, fields.wellness_ramp_rate),
        "resting_hr": _numeric_projection(payload, fields.wellness_resting_hr),
        "weight": _numeric_projection(payload, fields.wellness_weight),
        "weight_carried_over": payload[fields.wellness_weight_carried_over].as_boolean(),
        "resting_hr_carried_over": payload[fields.wellness_resting_hr_carried_over].as_boolean(),
    }


def _merged_payload(table: Table, incoming: ColumnElement, policy: UpsertPolicy) -> ColumnElement:
    if policy.explicit_nulls is NullPolicy.PRESERVE:
        # Do not strip nulls recursively: that would corrupt opaque nested values.
        # Retain a new explicit-null field (presence matters), but never replace
        # an existing field with null under the conservative preserve policy.
        pairs = func.jsonb_each(incoming).table_valued("key", "value")
        # INSERT is not a SELECT FROM: SQLAlchemy cannot auto-correlate its
        # target table. Reference the conflict row without adding an inner
        # table scan. The table name is fixed model metadata, never input.
        stored = literal_column(f"{table.name}.payload", type_=JSONB)
        incoming = (
            select(
                func.coalesce(
                    func.jsonb_object_agg(pairs.c.key, pairs.c.value),
                    literal({}, type_=JSONB),
                )
            )
            .where(
                or_(
                    pairs.c.value != literal(None, type_=JSONB),
                    stored.op("->")(pairs.c.key).is_(None),
                )
            )
            .scalar_subquery()
        )
    return table.c.payload.op("||", return_type=JSONB)(incoming)


def _reject_credentials(value: object) -> None:
    # ADR-025: raw activity payloads are retained, never auth/profile objects.
    # Inspect nested mappings too; never include field names/values in errors.
    forbidden_parts = (
        "apikey",
        "secret",
        "token",
        "password",
        "passwd",
        "bearer",
        "authorization",
        "credential",
        "privatekey",
        "accesskey",
        "cookie",
    )
    # Keep the previously forbidden non-credential objects and generic auth fields.
    forbidden_exact = {"auth", "headers", "profile", "athlete"}
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = "".join(char for char in key.lower() if char.isalnum())
            if normalized in forbidden_exact or any(part in normalized for part in forbidden_parts):
                raise UpsertError("credential_bearing_payload")
            _reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _reject_credentials(child)


def _upsert(
    session: Session,
    user_id: UUID,
    integration: UserIntegration,
    records: Sequence[ActivityRecord] | Sequence[WellnessRecord],
    model: type[Activity] | type[WellnessDay],
    policy: UpsertPolicy,
    observed_at: datetime | None,
) -> int:
    timestamp = observed_at if observed_at is not None else datetime.now(UTC)
    if timestamp.utcoffset() is None:
        raise UpsertError("invalid_observation_time")
    # Enrollment already uses "intervals"; retain exact persisted namespace,
    # while supporting the ADR-024 "intervals.icu" label (no rebinding).
    if integration.user_id != user_id or integration.provider not in ("intervals", "intervals.icu"):
        raise UpsertError("integration_owner_mismatch")
    # A detached/stale/modified integration cannot silently switch the binding.
    with session.no_autoflush:
        bound = session.scalar(
            select(UserIntegration.id).where(
                UserIntegration.id == integration.id,
                UserIntegration.user_id == user_id,
                UserIntegration.provider == integration.provider,
                UserIntegration.external_athlete_id == integration.external_athlete_id,
            )
        )
    if (
        bound is None
        or not integration.external_athlete_id
        or integration.external_athlete_id == "0"
    ):
        raise UpsertError("invalid_integration_binding")

    batch = []
    identities = set()
    for record in records:
        if isinstance(record, ActivityRecord) and model is Activity:
            if not isinstance(record.id, str):
                raise UpsertError("invalid_record_identity")
            identity = record.id
            raw_identity = identity
            if (
                record.athlete_id is not None
                and record.athlete_id != integration.external_athlete_id
            ):
                raise UpsertError("athlete_mismatch")
        elif isinstance(record, WellnessRecord) and model is WellnessDay:
            if type(record.local_date) is not date:
                raise UpsertError("invalid_record_identity")
            identity = record.local_date
            raw_identity = identity.isoformat()
        else:
            raise UpsertError("invalid_record")
        payload = dict(record.raw)
        _reject_credentials(payload)
        if not raw_identity or payload.get("id") != raw_identity:
            raise UpsertError("invalid_record_identity")
        if identity in identities:
            raise UpsertError("duplicate_record_identity")
        identities.add(identity)
        for name in policy.fields.athlete_ids:
            athlete = payload.get(name)
            if athlete is not None and athlete != integration.external_athlete_id:
                raise UpsertError("athlete_mismatch")
        batch.append((identity, payload))

    table = model.__table__
    assert isinstance(table, Table)
    identity_column = "provider_activity_id" if model is Activity else "local_date"
    changed = 0
    with session.begin_nested():
        # Stable ordering avoids opposite lock order for overlapping batches.
        for identity, payload in sorted(batch, key=lambda item: item[0]):
            incoming = bindparam("incoming_payload", payload, type_=JSONB)
            statement = insert(table).values(
                user_id=user_id,
                integration_id=integration.id,
                **{identity_column: identity},
                payload=incoming,
                **_projections(incoming, model, policy.fields),
                first_seen_at=timestamp,
                last_seen_at=timestamp,
                updated_at=timestamp,
            )
            merged = _merged_payload(table, statement.excluded.payload, policy)
            content = {"payload": merged, **_projections(merged, model, policy.fields)}
            statement = statement.on_conflict_do_update(
                constraint=f"uq_{table.name}_identity",
                set_={**content, "last_seen_at": timestamp, "updated_at": timestamp},
                where=or_(
                    *(table.c[name].is_distinct_from(value) for name, value in content.items())
                ),
            ).returning(table.c.id)
            changed += session.execute(statement).scalar_one_or_none() is not None
    return changed


def upsert_activities(
    session: Session,
    *,
    user_id: UUID,
    integration: UserIntegration,
    records: Sequence[ActivityRecord],
    policy: UpsertPolicy = DEFAULT_UPSERT_POLICY,
    observed_at: datetime | None = None,
) -> int:
    """Return inserted/changed row count; caller must commit. No delete path."""
    return _upsert(session, user_id, integration, records, Activity, policy, observed_at)


def upsert_wellness_days(
    session: Session,
    *,
    user_id: UUID,
    integration: UserIntegration,
    records: Sequence[WellnessRecord],
    policy: UpsertPolicy = DEFAULT_UPSERT_POLICY,
    observed_at: datetime | None = None,
) -> int:
    """Key by supplied local date; otherwise the same contract as activities."""
    return _upsert(session, user_id, integration, records, WellnessDay, policy, observed_at)
