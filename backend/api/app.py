"""FastAPI HTTP layer for the ESP design product.

The module intentionally orchestrates the immutable engine, agent, catalog, and
empirical layers.  It does not calculate any hydraulic or equipment values.

Requests are scoped by a two-level nested PERIMETER (framework v0.3 §9.2), not by
a flat tenant column.  Each perimeter has its own physical store, so the
isolation property of §9.4 holds without any WHERE clause in the trust path.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from esp_agents.contracts import NarrativeResult
from esp_agents.intake import IntakeAgent
from esp_agents.llm import PplxStructuredLLM, StructuredLLM
from esp_agents.narrative import NarrativeAgent, NarrativeCancelled
from esp_agents.trust import TrustLevel
from esp_empirical.application import OverlayTarget, apply_rules_as_overlay
from esp_empirical.database import PerimeterEmpiricalStores
from esp_empirical.models import DerivedEmpiricalRule, ObservationDraft
from esp_empirical.survival import RunLifeObservation, kaplan_meier
from esp_engine.catalog import Catalog, is_estimated_record, load_catalog
from esp_engine.curves import (
    curve_domain,
    scale_curve_to_frequency,
    try_evaluate_stage,
)
from esp_engine.config import DEFAULT_CONFIG, EngineConfig
from esp_engine.models import (
    Case,
    CaseMetadata,
    CasingSection,
    Constraints,
    DeviationSurveyPoint,
    ElectricalConstraints,
    Expectations,
    FluidProperties,
    ReservoirProperties,
    TaskType,
    TrajectorySpec,
    WellGeometry,
)
from esp_engine.pipeline import DesignEngine
from esp_engine.provenance import Assumption, BiasDirection, Source, Tracked
from esp_engine.results import DesignResult

from .jobs import (
    STAGE_ENGINE,
    STAGE_NARRATIVE,
    AdmissionRefused,
    JobCancelled,
    JobContext,
    JobRunner,
    RunnerLimits,
)
from esp_perimeter.deployment import (
    AgentMode,
    DeploymentPosture,
    EndpointLocation,
    classify_deployment,
    env_surface,
)
from esp_perimeter import (
    Perimeter,
    PerimeterLevelError,
    PerimeterStoreRegistry,
    PerimeterViolation,
    parse_perimeter,
)

# A request without perimeter headers is interpreted as the demo operator rather
# than as the org: everything the flat pre-§9 API stored (cases, designs,
# observations) is operator-level data, and defaulting it to org level would
# silently widen the perimeter of those rows.
DEFAULT_ORG_ID = "demo"
DEFAULT_OPERATOR_ID = "demo-operator"
DEMO_PERIMETER = Perimeter(org_id=DEFAULT_ORG_ID, operator_id=DEFAULT_OPERATOR_ID)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE_ROOT = PROJECT_ROOT / "data" / "perimeters"

_AGENTIC_OFF_VALUES = {"off", "0", "false", "no"}

# ``storage_root=None`` has to mean "in-memory stores", so the "argument not
# supplied" case needs a value of its own rather than reusing None.
_UNSET_STORAGE_ROOT = object()


def _agentic_default() -> bool:
    return os.environ.get("ESP_AGENTIC_LAYER", "on").strip().lower() not in _AGENTIC_OFF_VALUES


class ApiBase(DeclarativeBase):
    """Persistence owned by the HTTP layer; empirical models retain their own guard."""


class CaseRow(ApiBase):
    __tablename__ = "api_cases"

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Not the isolation mechanism. The store this row lives in was selected from
    # the perimeter before the statement existed (§9.4); this column only lets a
    # read detect that a store was misrouted, which a discriminator column used
    # for filtering could never detect about itself.
    perimeter_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    case_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    intake_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProjectPreferenceRow(ApiBase):
    __tablename__ = "api_project_preferences"
    project_id: Mapped[str] = mapped_column(String(128),primary_key=True)
    perimeter_key: Mapped[str] = mapped_column(String(255),nullable=False)
    preference_json: Mapped[str] = mapped_column(Text,nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(255),nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)


class DesignRow(ApiBase):
    __tablename__ = "api_designs"

    design_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    perimeter_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    case_id: Mapped[str] = mapped_column(ForeignKey("api_cases.case_id"), index=True, nullable=False)
    facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    judgments_json: Mapped[str] = mapped_column(Text, nullable=False)
    narrative_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    case_hash: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewRow(ApiBase):
    __tablename__ = "api_design_reviews"

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    design_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    perimeter_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    engineer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    final_decision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    agreed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    branch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    complication_segment: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# Terminal job states. ``interrupted`` is assigned on read when a stored job is
# still marked active but no live process holds it (the server restarted).
JOB_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
JOB_ACTIVE = frozenset({"queued", "running"})


class JobRow(ApiBase):
    """One design run or narrative retry, persisted in the perimeter's own store.

    Stored rather than kept only in memory so a client can still learn what
    happened to its run after a restart: a job the new process does not hold is
    reported as ``interrupted``, never left looking like it is still running.
    """

    __tablename__ = "api_design_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    perimeter_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    case_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # ``design`` runs both stages; ``narrative`` re-runs only the agent layer
    # against an existing stored design.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    # Case content + engine config + catalog version. Two submissions with the
    # same fingerprint while one is active are the same run.
    case_fingerprint: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    design_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    narrative_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    facts_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiService:
    """Composable service dependencies. Tests replace ``llm`` with a deterministic fake."""

    def __init__(
        self,
        storage_root: Path | None,
        llm: StructuredLLM | None = None,
        *,
        agentic: bool = True,
        runner_limits: RunnerLimits | None = None,
    ) -> None:
        self.runner = JobRunner(runner_limits)
        self.storage_root = Path(storage_root) if storage_root is not None else None
        self.registry = PerimeterStoreRegistry(
            self.storage_root,
            initializer=lambda engine: ApiBase.metadata.create_all(engine),
        )
        self.empirical_stores = PerimeterEmpiricalStores(self.registry)
        self.agentic = agentic
        # §9.1 requires that no access channel to customer data exists, and a
        # hosted model endpoint is an access channel. With the agentic layer off
        # the client is never constructed, so the absence of egress is a property
        # of the object graph rather than of a code path that happens not to run.
        self.llm: StructuredLLM | None = None
        if agentic:
            # PplxStructuredLLM authenticates from PPLX_LLM_API_* rather than the
            # OpenAI-style variables, so the same adapter works inside the server
            # and from a shell. That makes the narrative guard exercisable from
            # the CLI.
            self.llm = llm or PplxStructuredLLM()
        self.catalog: Catalog = load_catalog()
        self.engine_config: EngineConfig = DEFAULT_CONFIG

    def initialize(self) -> None:
        self.seed_demo_cases()

    def session(self, perimeter: Perimeter):
        """A committing session bound to ``perimeter``'s own store."""
        return self.registry.session(perimeter)

    def empirical(self, perimeter: Perimeter):
        """The empirical repository for ``perimeter``'s own store.

        The API engine is opened first because that is what creates this
        perimeter's directory under the storage root. The empirical store shares
        the same database file and would otherwise be the first opener of a path
        whose parent directory does not exist yet.
        """
        self.registry.engine_for(perimeter)
        return self.empirical_stores.for_perimeter(perimeter)

    def seed_demo_cases(self) -> None:
        """Give the separate-origin frontend immediately usable, honestly-provenanced data."""
        perimeter = DEMO_PERIMETER
        with self.session(perimeter) as session:
            exists = session.scalar(select(CaseRow.case_id).limit(1))
            from esp_engine.synthetic import demo_cases
            for case in (_demo_feasible_case(), _demo_missing_data_case(), _demo_unachievable_case(), *demo_cases()):
                if session.get(CaseRow, case.metadata.case_id) is not None:
                    continue
                payload = case.model_dump(mode="json", exclude_none=True)
                now = _now()
                session.add(CaseRow(
                    case_id=case.metadata.case_id,
                    perimeter_key=perimeter.key,
                    case_json=_json(payload),
                    raw_request=case.metadata.raw_request,
                    created_at=now,
                    updated_at=now,
                ))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _load_json(value: str | None, default: Any = None) -> Any:
    return json.loads(value) if value else default


def _perimeter(
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    x_operator_id: str | None = Header(default=None, alias="X-Operator-Id"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> Perimeter:
    """Resolve the caller's capsule from request headers.

    Malformed ids raise ``ValueError`` here and are turned into 422 by the
    handlers registered in :func:`create_app`; the exception messages are written
    to be shown to the caller.

    The demo operator default applies only when the request names no perimeter at
    all, which keeps the header-less demo working. A caller that sends X-Org-Id
    and deliberately omits X-Operator-Id is addressing the outer capsule, so no
    operator is substituted -- otherwise org level would be unaddressable and the
    level check of §9.2 could never fire. That rule lives in ``parse_perimeter``
    rather than here, so a second entry point cannot re-derive it differently.
    """
    return parse_perimeter(
        org_id=x_org_id,
        operator_id=x_operator_id,
        legacy_tenant_id=x_tenant_id,
        default_org=DEFAULT_ORG_ID,
        default_operator=DEFAULT_OPERATOR_ID,
    )


def _get_service(request: Request) -> ApiService:
    return request.app.state.service


def _value_error_detail(exc: BaseException) -> str:
    """The caller-facing message of a perimeter validation failure.

    Perimeter ids are validated by pydantic, so a bad id arrives wrapped in a
    ``ValidationError``. The wrapper's repr names internal field paths; the inner
    message is the one written for a human.
    """
    if isinstance(exc, ValidationError):
        messages = [
            str(error.get("msg", "")).removeprefix("Value error, ") for error in exc.errors()
        ]
        return " ".join(message for message in messages if message) or str(exc)
    return str(exc)


def _assert_row_perimeter(row: Any, perimeter: Perimeter) -> Any:
    """Tripwire for a misrouted store, not an access check.

    Reaching this branch means a row physically stored in this perimeter's
    database was written with another perimeter's stamp, i.e. the store selection
    of §9.4 was bypassed. That is a server fault, not a client error, and the
    request must fail rather than present the row.
    """
    if row.perimeter_key != perimeter.key:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Store misrouting detected: a row stamped '{row.perimeter_key}' was read from "
                f"the store of perimeter '{perimeter.key}'. Refusing to present it."
            ),
        )
    return row


def _get_case_row(session: Session, perimeter: Perimeter, case_id: str) -> CaseRow:
    # Deliberately unfiltered by perimeter: the session is already bound to this
    # perimeter's own database, so there is no other perimeter's row to exclude.
    row = session.scalar(select(CaseRow).where(CaseRow.case_id == case_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Case not found in this perimeter.")
    return _assert_row_perimeter(row, perimeter)


def _get_design_row(session: Session, perimeter: Perimeter, design_id: str) -> DesignRow:
    row = session.scalar(select(DesignRow).where(DesignRow.design_id == design_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Design not found in this perimeter.")
    return _assert_row_perimeter(row, perimeter)


def _get_job_row(session: Session, perimeter: Perimeter, job_id: str) -> JobRow:
    row = session.scalar(select(JobRow).where(JobRow.job_id == job_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found in this perimeter.")
    return _assert_row_perimeter(row, perimeter)


def _case_from_row(row: CaseRow) -> Case:
    if not row.case_json:
        raise HTTPException(status_code=409, detail="Case has raw input but no validated structured Case. Run intake first.")
    try:
        return Case.model_validate(_load_json(row.case_json))
    except ValidationError as exc:  # Never let corrupt persistence become a design calculation.
        raise HTTPException(status_code=500, detail=f"Persisted Case is invalid: {exc}") from exc


def _normalize_case(payload: dict[str, Any], perimeter: Perimeter, case_id: str | None = None) -> Case:
    """API owns perimeter and identity; callers may not submit a cross-perimeter Case."""
    working = dict(payload.get("case", payload))
    engineering=dict(working.get("engineering") or {})
    engineering["applied_project_preference"]=None
    working["engineering"]=engineering
    metadata = dict(working.get("metadata") or {})
    metadata["case_id"] = case_id or metadata.get("case_id") or str(uuid.uuid4())
    # The engine's Case still carries one isolation string; it now holds the
    # perimeter key so a stored Case names the capsule that owns it.
    metadata["tenant_id"] = perimeter.key
    metadata.setdefault("created_at", _now().isoformat())
    working["metadata"] = metadata
    try:
        return Case.model_validate(working)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc


def _serialize_case(case: Case) -> dict[str, Any]:
    return case.model_dump(mode="json", exclude_none=True)


def _perimeter_fields(perimeter: Perimeter) -> dict[str, Any]:
    return {
        "perimeter": perimeter.key,
        "perimeter_level": perimeter.level.value,
        "org_id": perimeter.org_id,
        "operator_id": perimeter.operator_id,
    }


def _facts_and_judgments(
    result: DesignResult,
    narrative: NarrativeResult | dict[str, Any] | None,
    *,
    evidence_refusals: list[str],
    narrative_disabled_reason: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    # This is the product boundary: facts never contain the result's judgment list.
    facts = result.model_dump(mode="json", exclude={"judgments"})
    empirical = [item.model_dump(mode="json") for item in result.judgments]
    narrative_payload = narrative.model_dump(mode="json") if isinstance(narrative, NarrativeResult) else narrative
    return facts, {
        "empirical": empirical,
        "narrative": narrative_payload,
        # Stated rather than left as a bare null: a missing narrative and a
        # deliberately severed narrative layer are different facts about the
        # deployment (§6A.3).
        "narrative_disabled_reason": narrative_disabled_reason,
        # Evidence dropped at the presentation boundary is named here, because
        # silently omitting it would make an out-of-perimeter rule look like an
        # absence of evidence (§9.3).
        "evidence_refusals": list(evidence_refusals),
    }


def _present_design(row: DesignRow, service: "ApiService | None" = None) -> dict[str, Any]:
    judgments = _load_json(row.judgments_json, {})
    narrative = judgments.get("narrative")
    if (
        service is not None
        and isinstance(narrative, dict)
        and narrative.get("status") == "pending"
        and not service.runner.is_live(str(narrative.get("job_id")))
    ):
        # A pending narrative whose job no live process holds will never arrive.
        # Say so instead of leaving the UI waiting on it.
        judgments["narrative"] = {
            **narrative,
            "status": "interrupted",
            "refusal_reason": _interrupted_reason(STAGE_NARRATIVE),
        }
    return {
        "id": row.design_id,
        "case_id": row.case_id,
        "facts": _load_json(row.facts_json, {}),
        "judgments": judgments,
        "reproducibility": {
            "case_hash": row.case_hash,
            "config_hash": row.config_hash,
            "catalog_version": row.catalog_version,
            "engine_version": row.engine_version,
        },
        "created_at": row.created_at.isoformat(),
    }


def _segment_for(case: Case) -> tuple[str, str, str]:
    complication = "gassy" if case.complications.is_gassy else "standard"
    confidence = case.overall_input_confidence
    band = "high" if confidence >= 0.85 else "medium" if confidence >= 0.70 else "low"
    return case.branch.value, complication, band


def _record_review(session: Session, *, perimeter: Perimeter, case: Case, design_id: str | None, action: str, body: dict[str, Any]) -> ReviewRow:
    if action not in {"accept", "override", "escalate"}:
        raise HTTPException(status_code=422, detail="action must be accept, override, or escalate.")
    if action == "override" and not body.get("override_reason"):
        raise HTTPException(status_code=422, detail="override_reason is required for an override.")
    branch, complication, confidence = _segment_for(case)
    review = ReviewRow(
        review_id=str(uuid.uuid4()), design_id=design_id, case_id=case.metadata.case_id,
        perimeter_key=perimeter.key, action=action, engineer_id=body.get("engineer_id"),
        final_decision_json=_json(body.get("final_decision")) if body.get("final_decision") is not None else None,
        override_reason=body.get("override_reason"), agreed=action == "accept",
        branch=branch, complication_segment=complication, confidence_band=confidence, created_at=_now(),
    )
    session.add(review)
    return review


def _evidence_refusal_reason(rule: DerivedEmpiricalRule, perimeter: Perimeter) -> str:
    return (
        f"Evidence for rule {rule.rule_id} was derived from data in perimeter "
        f"'{rule.evidence.source_perimeter}' and is refused in '{perimeter.key}'. A rule inherits "
        "the perimeter of the data it came from (§9.3): its observation counts, field counts and "
        "effect sizes describe another capsule's wells, so they are withheld rather than restated "
        "without names."
    )


def _present_rule(rule: DerivedEmpiricalRule, perimeter: Perimeter) -> dict[str, Any]:
    """Serialize a rule, withholding out-of-perimeter evidence explicitly.

    The check is at the presentation boundary rather than the storage boundary
    because a rule object can reach here in process -- through an overlay, a
    report, or a cached response -- without passing store selection again.
    """
    payload = rule.model_dump(mode="json")
    if rule.evidence_visible_in(perimeter.key):
        payload["evidence_withheld"] = False
        return payload
    payload["evidence"] = None
    payload["supporting_observation_ids"] = []
    payload["survival_summary"] = None
    payload["evidence_withheld"] = True
    payload["evidence_withheld_reason"] = _evidence_refusal_reason(rule, perimeter)
    return payload


def _apply_empirical_overlay(
    service: ApiService, result: DesignResult, perimeter: Perimeter
) -> tuple[DesignResult, list[str]]:
    """Attach advisory judgments, refusing any whose evidence is out of perimeter."""
    best = result.best
    if best is None:
        return result, []
    target = OverlayTarget(
        design_id=result.design_id, tenant_id=perimeter.key,
        pump_model=best.configuration.pump_model,
        setting_depth_md_ft=best.configuration.setting_depth_md_ft,
        gfv_frac=best.design_point.free_gas_fraction_at_intake,
        fact_ids=(f"candidate_{best.rank}.pump", f"candidate_{best.rank}.tdh_ft", f"candidate_{best.rank}.zone"),
    )
    with service.empirical(perimeter) as repository:
        overlay = apply_rules_as_overlay(target, repository)
        rules_by_id = {rule.rule_id: rule for rule in repository.list_derived_rules()}

    kept = []
    refusals: list[str] = []
    for judgment in overlay.judgments:
        # A judgment carries the rule's evidence strength and confidence basis, so
        # attaching one whose evidence is out of perimeter would export the
        # evidence in prose. Drop the whole overlay entry and say why.
        blocked = [
            rules_by_id[rule_id]
            for rule_id in judgment.supporting_rule_ids
            if rule_id in rules_by_id and not rules_by_id[rule_id].evidence_visible_in(perimeter.key)
        ]
        if blocked:
            refusals.extend(_evidence_refusal_reason(rule, perimeter) for rule in blocked)
            continue
        kept.append(judgment)
    return result.model_copy(update={"judgments": kept}), refusals


_NARRATIVE_DISABLED_REASON = (
    "The agentic narrative layer is disabled in this deployment (ESP_AGENTIC_LAYER=off), so no "
    "hosted model endpoint is contacted. Framework §6A.3 places the whole agentic layer after the "
    "deterministic TDH path, so it is severable: facts, candidate ranking and empirical judgments "
    "below are produced without it and are unchanged by its absence."
)

_INTAKE_DISABLED_REASON = (
    "Unstructured intake is part of the agentic layer, which is disabled in this deployment "
    "(ESP_AGENTIC_LAYER=off). Framework §9.1 requires that no access channel to this perimeter's "
    "data exists, and a hosted model endpoint is an access channel; this deployment therefore has "
    "none. Free text cannot be turned into a Case here. Supply a structured Case directly to "
    "PUT /api/cases/{case_id} (PATCH is accepted as well) and then run POST "
    "/api/cases/{case_id}/design."
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}.") from exc


def _runner_limits_from_env() -> RunnerLimits:
    defaults = RunnerLimits()
    return RunnerLimits(
        engine_workers=_env_int("ESP_ENGINE_WORKERS", defaults.engine_workers),
        narrative_workers=_env_int("ESP_NARRATIVE_WORKERS", defaults.narrative_workers),
        max_active_jobs=_env_int("ESP_MAX_ACTIVE_JOBS", defaults.max_active_jobs),
        max_active_jobs_per_perimeter=_env_int(
            "ESP_MAX_ACTIVE_JOBS_PER_PERIMETER", defaults.max_active_jobs_per_perimeter
        ),
    )


def _case_fingerprint(service: ApiService, case_json: str) -> str:
    digest = hashlib.sha256()
    digest.update(case_json.encode())
    digest.update(_json(service.engine_config.model_dump(mode="json")).encode())
    digest.update(service.catalog.version.encode())
    return digest.hexdigest()


def _interrupted_reason(stage: str) -> str:
    return (
        f"The server restarted while this run was in its {stage} stage, so no process "
        "holds it any more. Nothing was lost that had already been stored: if a design_id "
        "is set, its facts are complete. Submit the run again, or retry only the narrative "
        "with POST /api/designs/{design_id}/narrative."
    )


def _pending_narrative(job_id: str) -> dict[str, Any]:
    return {
        "status": "pending",
        "job_id": job_id,
        "refusal_reason": None,
        "attempts": 0,
        "note": (
            "Facts are final. The narrative is generated separately after the "
            "deterministic stage and attaches to this design when it completes."
        ),
    }


def _update_job(service: ApiService, perimeter: Perimeter, job_id: str, **fields: Any) -> None:
    with service.session(perimeter) as session:
        row = session.scalar(select(JobRow).where(JobRow.job_id == job_id))
        if row is None:
            return
        _assert_row_perimeter(row, perimeter)
        for key, value in fields.items():
            setattr(row, key, value)


def _attach_narrative(
    service: ApiService, perimeter: Perimeter, design_id: str, narrative: dict[str, Any]
) -> None:
    """Write the narrative judgment onto a stored design.

    Only the narrative slot changes. ``facts_json`` is written once by the
    engine stage and never touched again, so a design's facts are immutable
    from the moment they are readable.
    """
    with service.session(perimeter) as session:
        row = _get_design_row(session, perimeter, design_id)
        judgments = _load_json(row.judgments_json, {})
        judgments["narrative"] = narrative
        row.judgments_json = _json(judgments)
        row.narrative_json = _json(narrative)


def _engine_stage(service: ApiService, perimeter: Perimeter, job_id: str, case_id: str):
    def run(ctx: JobContext) -> None:
        handed_off = False
        try:
            ctx.raise_if_cancelled()
            _update_job(service, perimeter, job_id, status="running", phase=STAGE_ENGINE, started_at=_now())
            with service.session(perimeter) as session:
                case = _case_from_row(_get_case_row(session, perimeter, case_id))
                if case.engineering.apply_project_preference and case.metadata.project_id:
                    preference = session.get(ProjectPreferenceRow,case.metadata.project_id)
                    if preference is not None:
                        _assert_row_perimeter(preference,perimeter)
                        policy=json.loads(preference.preference_json)
                        case=case.model_copy(update={"engineering":case.engineering.model_copy(update={
                            "applied_project_preference":policy,
                            "ranking_mode":policy["ranking_mode"],
                        })})
            # DesignEngine owns all numbers; the API passes the frozen Case across.
            from esp_engine.synthetic import synthetic_catalog
            engine = DesignEngine(catalog=synthetic_catalog() if case.synthetic else service.catalog, config=service.engine_config)
            result, refusals = _apply_empirical_overlay(service, engine.run(case), perimeter)
            ctx.raise_if_cancelled()
            if service.agentic:
                narrative: dict[str, Any] | None = _pending_narrative(job_id)
                disabled = None
            else:
                # 6A.3: severing the agentic layer removes prose and nothing else.
                narrative, disabled = None, _NARRATIVE_DISABLED_REASON
            facts, judgments = _facts_and_judgments(
                result, narrative, evidence_refusals=refusals, narrative_disabled_reason=disabled
            )
            now = _now()
            with service.session(perimeter) as session:
                session.add(DesignRow(
                    design_id=result.design_id, perimeter_key=perimeter.key, case_id=case_id,
                    facts_json=_json(facts), judgments_json=_json(judgments),
                    narrative_json=_json(narrative),
                    case_snapshot_json=_json(_serialize_case(case)),
                    config_snapshot_json=_json(engine.config.model_dump(mode="json")),
                    catalog_snapshot_json=_json(engine.catalog.model_dump(mode="json")),
                    case_hash=result.provenance.case_hash, config_hash=result.provenance.config_hash,
                    catalog_version=result.provenance.catalog_version,
                    engine_version=result.provenance.engine_version, created_at=now,
                ))
            if service.agentic:
                _update_job(
                    service, perimeter, job_id, design_id=result.design_id, facts_ready_at=now,
                    phase=STAGE_NARRATIVE, narrative_status="pending",
                )
                service.runner.submit(
                    job_id, STAGE_NARRATIVE,
                    _narrative_stage(service, perimeter, job_id, result.design_id),
                )
                handed_off = True
            else:
                _update_job(
                    service, perimeter, job_id, design_id=result.design_id, facts_ready_at=now,
                    finished_at=now, status="succeeded", phase="done", narrative_status="disabled",
                )
        except JobCancelled:
            _safe_update(service, perimeter, job_id, status="cancelled", phase="done", finished_at=_now(),
                         error="Cancelled before the deterministic stage stored any facts.")
        except HTTPException as exc:
            _safe_update(service, perimeter, job_id, status="failed", phase="done", finished_at=_now(),
                         error=str(exc.detail))
        except Exception as exc:  # noqa: BLE001 - reported on the job, not swallowed
            _safe_update(service, perimeter, job_id, status="failed", phase="done", finished_at=_now(),
                         error=f"{type(exc).__name__}: {exc}")
        finally:
            if not handed_off:
                service.runner.finish(job_id)

    return run


def _narrative_stage(service: ApiService, perimeter: Perimeter, job_id: str, design_id: str):
    def run(ctx: JobContext) -> None:
        try:
            ctx.raise_if_cancelled()
            _update_job(service, perimeter, job_id, status="running", phase=STAGE_NARRATIVE)
            with service.session(perimeter) as session:
                row = _get_design_row(session, perimeter, design_id)
                facts = _load_json(row.facts_json, {})
                empirical = _load_json(row.judgments_json, {}).get("empirical") or []
            # Rebuilt from storage rather than handed over in memory, so this stage
            # is the same code whether it follows the engine stage or is a retry
            # after a restart. The rebuild is exact (asserted in tests).
            result = DesignResult.model_validate({**facts, "judgments": empirical})
            try:
                outcome = NarrativeAgent(service.llm).run(result, should_stop=ctx.cancelled)
                narrative = outcome.model_dump(mode="json")
            except NarrativeCancelled as exc:
                raise JobCancelled(job_id) from exc
            except Exception as exc:  # noqa: BLE001 - the facts stay useful without prose
                narrative = {"status": "refused", "refusal_reason": f"Narrative unavailable: {exc}", "attempts": 0}
            # A model call already in flight when cancel arrived still returns;
            # its prose is discarded rather than attached to a cancelled run.
            ctx.raise_if_cancelled()
            _attach_narrative(service, perimeter, design_id, narrative)
            _update_job(service, perimeter, job_id, status="succeeded", phase="done",
                        finished_at=_now(), narrative_status=narrative.get("status"))
        except JobCancelled:
            _safe_attach(service, perimeter, design_id, _cancelled_narrative(job_id))
            _safe_update(service, perimeter, job_id, status="cancelled", phase="done", finished_at=_now(),
                         narrative_status="cancelled",
                         error="Cancelled during the narrative stage. The design's facts were already stored and are unaffected.")
        except Exception as exc:  # noqa: BLE001
            _safe_attach(service, perimeter, design_id, {
                "status": "refused", "refusal_reason": f"Narrative stage failed: {type(exc).__name__}: {exc}", "attempts": 0,
            })
            _safe_update(service, perimeter, job_id, status="failed", phase="done", finished_at=_now(),
                         narrative_status="refused", error=f"{type(exc).__name__}: {exc}")
        finally:
            service.runner.finish(job_id)

    return run


def _cancelled_narrative(job_id: str) -> dict[str, Any]:
    return {"status": "cancelled", "job_id": job_id, "attempts": 0,
            "refusal_reason": "The narrative was cancelled before it completed. The facts are unaffected."}


def _safe_update(service: ApiService, perimeter: Perimeter, job_id: str, **fields: Any) -> None:
    # Terminal bookkeeping must not raise out of a worker thread (for example
    # while the store is being disposed at shutdown); the job then reads as
    # interrupted on the next start, which is the truthful description.
    try:
        _update_job(service, perimeter, job_id, **fields)
    except Exception:  # noqa: BLE001
        pass


def _safe_attach(service: ApiService, perimeter: Perimeter, design_id: str, narrative: dict[str, Any]) -> None:
    try:
        _attach_narrative(service, perimeter, design_id, narrative)
    except Exception:  # noqa: BLE001
        pass


def _reconcile_job(service: ApiService, row: JobRow) -> JobRow:
    """Mark a stored-active job that no live process holds as interrupted."""
    if row.status in JOB_ACTIVE and not service.runner.is_live(row.job_id):
        row.error = _interrupted_reason(row.phase)
        row.status = "interrupted"
        row.finished_at = row.finished_at or _now()
        if row.design_id and row.narrative_status == "pending":
            row.narrative_status = "interrupted"
    return row


def _present_job(row: JobRow, service: "ApiService | None" = None) -> dict[str, Any]:
    end = row.finished_at or _now()
    start = row.started_at or row.created_at
    facts_s = (
        (row.facts_ready_at - start).total_seconds() if row.facts_ready_at and row.started_at else None
    )
    links: dict[str, str] = {
        "self": f"/api/jobs/{row.job_id}",
        "cancel": f"/api/jobs/{row.job_id}/cancel",
    }
    if row.design_id:
        links["design"] = f"/api/designs/{row.design_id}"
    return {
        "job_id": row.job_id,
        "kind": row.kind,
        "case_id": row.case_id,
        "status": row.status,
        "phase": row.phase,
        "terminal": row.status in JOB_TERMINAL,
        "design_id": row.design_id,
        # True once the deterministic stage has stored the design: the facts can
        # be shown while the narrative is still pending.
        "facts_ready": row.design_id is not None,
        "narrative_status": row.narrative_status,
        # A running narrative cannot abort a model call already in flight; this
        # says cancel was accepted and the job ends when that call returns.
        "cancel_requested": bool(
            service is not None and row.status in JOB_ACTIVE and service.runner.cancel_requested(row.job_id)
        ),
        "error": row.error,
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "facts_ready_at": _iso(row.facts_ready_at),
        "finished_at": _iso(row.finished_at),
        "elapsed_s": round(max(0.0, (_aware(end) - _aware(start)).total_seconds()), 2),
        "seconds_to_facts": round(facts_s, 2) if facts_s is not None else None,
        "links": links,
    }


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes even for timezone=True columns.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value else None


def _render_report(presentation: dict[str, Any]) -> str:
    facts = presentation["facts"]
    lines = ["# ESP Design Report", "", f"Design ID: {presentation['id']}", f"Verdict: {facts['verdict']}", "", "## Deterministic facts", facts["verdict_explanation"]]
    if facts.get("candidates"):
        lines.extend(["", "### Ranked candidates"])
        for candidate in facts["candidates"]:
            config = candidate["configuration"]
            lines.append(f"- #{candidate['rank']}: {config['pump_model']}, {config['stages']} stages, {config['frequency_hz']} Hz")
    lines.extend(["", "## Judgments (advisory; do not modify facts)"])
    for judgment in presentation["judgments"].get("empirical", []):
        lines.append(f"- {judgment['statement']}")
    for refusal in presentation["judgments"].get("evidence_refusals", []):
        lines.append(f"- Evidence withheld: {refusal}")
    narrative = presentation["judgments"].get("narrative") or {}
    if narrative.get("narrative"):
        lines.append(f"- Narrative: {narrative['narrative'].get('fact_summary', '')}")
    disabled = presentation["judgments"].get("narrative_disabled_reason")
    if disabled:
        lines.append(f"- Narrative: {disabled}")
    return "\n".join(lines)


def _curve_caveats(points: list[dict[str, Any]], *, is_estimated: bool) -> list[str]:
    """Caveats a client must display alongside a rendered curve.

    Returned from the API rather than hardcoded in the frontend so that any
    consumer of this endpoint inherits them. A chart is the most persuasive
    artifact this product produces, and an unqualified curve drawn from an
    estimate is the most likely way for it to mislead.
    """
    caveats: list[str] = []
    if is_estimated:
        caveats.append(
            "This curve is a parametric estimate, not a digitized vendor curve. "
            "Head, efficiency, and thrust-zone boundaries are approximate and are "
            "not suitable for field design without vendor data."
        )
    # The fitted efficiency polynomial is not constrained to pass through zero at
    # shut-in, so it reports a nonzero efficiency at zero flow. Reporting the
    # artifact is better than silently trimming the domain and implying the fit is
    # physical where it is not.
    shut_in = next((p for p in points if p["q_bpd"] == 0.0), None)
    if shut_in is not None and shut_in["efficiency_frac"] > 0.01:
        caveats.append(
            "The fitted efficiency polynomial is not constrained to zero at "
            f"shut-in and reports {shut_in['efficiency_frac']:.3f} at zero flow, "
            "which is nonphysical. Disregard efficiency near zero flow."
        )
    return caveats


def _vendor_access_prose(service: "ApiService", posture: "DeploymentPosture") -> str:
    """Say what a third-party vendor can see under the current §9.7 posture.

    Three distinct realities, three distinct sentences: agent off; agent on
    with the endpoint outside the perimeter; agent on with the endpoint inside.
    Written from the classifier's result rather than a boolean, so a customer
    running a local Ollama does not read a paragraph about a third-party host
    that isn't in the picture.
    """
    if not service.agentic:
        return (
            "None of either kind. No store is reachable from outside its storage root, "
            "no code path exports one, and with the agentic layer off no case data leaves "
            "the process (§9.1). This is §9.7 mode 3 (deterministic)."
        )
    if posture.endpoint_location is EndpointLocation.INSIDE_PERIMETER:
        return (
            "No store access in either mode. With the agentic layer ON in §9.7 mode 1 "
            "(local model on customer infrastructure), the intake text and design facts "
            "are sent to an endpoint that resolves inside the perimeter, so no third-party "
            "vendor has access to this deployment's data. The customer, not the tool "
            "developer, operates that endpoint; the tool developer holds no capsule (§9.1). "
            "Cross-check the resolved address in agentic_layer.resolved_address."
        )
    return (
        "No store access in either mode: nothing in this deployment can open a store "
        "outside its own storage root and no code path exports one, so the tool "
        "developer holds no capsule (§9.1). Data access is a separate question from "
        "store access. With the agentic layer ON in §9.7 mode 2 (external API) it is "
        "not zero: the intake text and the design facts listed under egress_channels "
        "are sent to a hosted model endpoint operated by a third party under the "
        "customer's enterprise agreement, and that is an access channel to this "
        "perimeter's data regardless of retention policy. Set ESP_AGENTIC_LAYER=off "
        "to leave no access channel of either kind."
    )


def _llm_egress_entry(posture: "DeploymentPosture", service: "ApiService") -> dict[str, Any]:
    """Build the LLM egress entry from a §9.7 classification.

    Extracted so the wording is derived from the classifier's structured
    result rather than reassembled ad-hoc inside the handler. A test that pins
    the payload fields (``channel``, ``endpoint_location``, ``crosses_perimeter``)
    can then hold across prose changes.
    """
    location = posture.endpoint_location
    crosses = location is not EndpointLocation.INSIDE_PERIMETER
    channel = (
        "local_model_endpoint"
        if location is EndpointLocation.INSIDE_PERIMETER
        else "external_model_endpoint"
    )
    if location is EndpointLocation.INSIDE_PERIMETER:
        consequence = (
            "This endpoint resolves to an address inside the perimeter (§9.7 mode 1), so "
            "the request stays on customer infrastructure. The intake text and the design "
            "facts summarised by the narrative agent do not leave the deployment. Set "
            "ESP_AGENTIC_LAYER=off to switch to §9.7 mode 3 (deterministic)."
        )
    elif location is EndpointLocation.UNRESOLVED:
        consequence = (
            "The endpoint hostname did not resolve from this process. Under §9.7 this is "
            "reported as an outside-perimeter channel because a name that cannot be "
            "resolved cannot be shown to a security reviewer as safe."
        )
    elif location is EndpointLocation.MISCONFIGURED:
        consequence = (
            "The agentic layer is on but no valid endpoint is configured. The next call "
            "to the narrative agent will fail. Set PPLX_LLM_API_ADDRESS or disable the "
            "agentic layer."
        )
    else:
        consequence = (
            "This endpoint resolves to an address outside the perimeter (§9.7 mode 2). "
            "Under a customer enterprise agreement this is an acceptable channel, but it "
            "IS an egress channel: the operator's raw request text and design facts leave "
            "the deployment. Set ESP_AGENTIC_LAYER=off to remove it; the deterministic "
            "design path is unaffected (§6A.3)."
        )
    return {
        "channel": channel,
        "endpoint": posture.configured_endpoint or "PPLX_LLM_API_ADDRESS (not configured)",
        "endpoint_host": posture.endpoint_host,
        "resolved_address": posture.resolved_address,
        "endpoint_location": location.value,
        "crosses_perimeter": crosses,
        "client": type(service.llm).__name__,
        "what_is_sent": (
            "Unstructured intake text supplied to POST /api/cases/{id}/intake, and the "
            "deterministic design facts summarised by the narrative agent, are sent to "
            "the model endpoint named above."
        ),
        "consequence": consequence,
    }


def create_app(
    *,
    storage_root: Path | str | None = _UNSET_STORAGE_ROOT,  # type: ignore[assignment]
    llm: StructuredLLM | None = None,
    seed: bool = True,
    agentic: bool | None = None,
    runner_limits: RunnerLimits | None = None,
) -> FastAPI:
    """Build the app.

    ``storage_root`` is the root of the per-perimeter store tree. ``None`` selects
    in-memory stores, which still give each perimeter its own database.
    ``agentic=False`` severs the agent layer entirely: no model client is
    constructed, so §9.1's "no access channel exists" holds structurally.
    """
    if storage_root is _UNSET_STORAGE_ROOT:
        env_root = os.environ.get("ESP_STORAGE_ROOT")
        root: Path | None = Path(env_root) if env_root else DEFAULT_STORAGE_ROOT
    else:
        root = Path(storage_root) if storage_root is not None else None
    agentic_enabled = _agentic_default() if agentic is None else agentic
    service = ApiService(
        root, llm=llm, agentic=agentic_enabled,
        runner_limits=runner_limits or _runner_limits_from_env(),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if seed:
            service.seed_demo_cases()
        yield
        service.runner.shutdown()
        service.registry.dispose()

    app = FastAPI(title="ESP Design Automation API", version="0.3.0", lifespan=lifespan)
    app.state.service = service
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

    # Registered app-wide so every endpoint reports a perimeter failure the same
    # way, whether it came from header parsing or from a require_* call deep in a
    # handler. The exception messages are written for the caller and are used
    # verbatim as the detail.
    @app.exception_handler(PerimeterViolation)
    async def _perimeter_violation(request: Request, exc: PerimeterViolation) -> JSONResponse:
        # 403, not 404: the caller asked for something that exists somewhere and
        # is refused here. Crossing a capsule needs a human, not a retry (§9.3).
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(PerimeterLevelError)
    async def _perimeter_level(request: Request, exc: PerimeterLevelError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _bad_perimeter_id(request: Request, exc: ValueError) -> JSONResponse:
        # Perimeter ids are path segments in the store tree, so an invalid id is
        # rejected as caller input rather than sanitised into something valid.
        return JSONResponse(status_code=422, content={"detail": _value_error_detail(exc)})

    @app.get("/api/deployment")
    def get_deployment(
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        """The deployment's confidentiality posture, for the §9.6/§9.7 review.

        The framework's §9.7 rule is that this endpoint must report *where the
        model endpoint actually points*, not which mode is nominally selected:
        otherwise it reproduces the same self-contradicting posture claim as
        loading fonts from an external CDN. The classification below is
        performed against the resolved host, not against an operator-declared
        mode. If the two disagree, the disagreement is surfaced as
        ``posture_contradiction`` in the payload.

        §9.7 modes 1 and 2 (local model on customer infrastructure vs. external
        API under a customer enterprise agreement) collapse into one code path
        here. They differ only in the value of the endpoint URL. Deterministic
        mode (§9.7 mode 3) is the mandatory fallback: with the agent layer
        disabled no endpoint exists and the posture reflects that.
        """
        posture = classify_deployment(agentic_override=service.agentic)
        model_endpoint = posture.configured_endpoint
        egress: list[dict[str, Any]] = []
        if service.agentic and posture.effective_mode is not AgentMode.DETERMINISTIC:
            egress.append(_llm_egress_entry(posture, service))
        return {
            "perimeter_model": {
                "levels": 2,
                "shape": "nested",
                "outer": "org - the oilfield service company: equipment catalog and internal standards",
                "inner": "operator - wells, run-life history, teardown findings, derived empirical rules",
                "sibling_operators_never_merge": True,
                "framework_sections": ["9.2", "9.3", "9.4"],
            },
            "caller": _perimeter_fields(perimeter),
            "storage": {
                "per_perimeter_physical_stores": True,
                "storage_root": str(service.registry.root) if service.registry.root else None,
                "in_memory": service.registry.is_in_memory,
                "store_for_caller": service.registry.url_for(perimeter),
                "isolation_mechanism": (
                    "The database is selected from the perimeter before any statement is built, so "
                    "an unscoped SELECT inside one capsule cannot name another capsule's rows "
                    "(§9.4). Rows also carry a perimeter_key stamp, used only as a misrouting "
                    "tripwire, never as the filter."
                ),
                "second_mechanism": (
                    "Empirical queries additionally run through a fail-closed session guard that "
                    "refuses a SELECT without a scope."
                ),
            },
            "agentic_layer": {
                "enabled": service.agentic,
                "model_client": type(service.llm).__name__ if service.llm is not None else None,
                "model_endpoint": model_endpoint,
                "severable": True,
                # §9.7: report reality, not the nominal declaration. The two
                # can disagree, and if they do that is the fact the reviewer
                # needs first.
                "declared_mode": posture.declared_mode.value if posture.declared_mode else None,
                "effective_mode": posture.effective_mode.value,
                "endpoint_location": posture.endpoint_location.value,
                "endpoint_host": posture.endpoint_host,
                "resolved_address": posture.resolved_address,
                "inside_perimeter_override_applied": posture.inside_override_applied,
                "posture_contradiction": posture.posture_contradiction,
                "posture_contradiction_reason": posture.contradiction_reason,
                "config_env_surface": [
                    {"name": name, "purpose": purpose} for name, purpose in env_surface()
                ],
                "note": (
                    "Intake extraction and narrative prose only. It sits after the deterministic "
                    "TDH path (§6A.3) and cannot alter a fact, a candidate ranking or a verdict."
                    if service.agentic
                    else _INTAKE_DISABLED_REASON
                ),
            },
            "network_egress_from_design_path": bool(egress),
            "egress_channels": egress,
            # The browser is the other half of the perimeter and this process
            # cannot see it. Until this was stated explicitly, the payload's
            # "nothing leaves the perimeter" was scoped to the backend while the
            # console's index.html still loaded a Google Fonts stylesheet, which
            # handed a third party the client IP, user agent and a timestamp for
            # every design opened. The claim below is therefore labelled with
            # how it is enforced rather than simply asserted -- a posture report
            # that cannot be checked is worth nothing to a security reviewer.
            "browser_egress": {
                "third_party_requests": False,
                "fonts": "self-hosted, bundled from the @fontsource packages and served "
                "from the application's own origin",
                "analytics_or_error_reporting": None,
                "verified_by": "backend/tests/test_no_external_egress.py",
                "enforcement": (
                    "Build-time, not runtime: this process serves the API and cannot "
                    "observe what the browser fetches. The guard inspects both "
                    "frontend/index.html and the built bundle for external hosts, "
                    "connection hints and CSS url() targets, so a regression fails "
                    "the test suite instead of shipping. Confirm against the bundle "
                    "you were given rather than trusting this field."
                ),
            },
            "leaves_the_perimeter": (
                [entry["what_is_sent"] for entry in egress]
                if egress
                else [
                    "Nothing. With the agentic layer off there is no network egress from the "
                    "design path: the deterministic engine, the catalog and the empirical store "
                    "are all local, and no model client is constructed in this process."
                ]
            ),
            # Stated conditionally on purpose. A blanket "no vendor access" claim in the
            # same payload that discloses egress to a hosted model endpoint is the
            # contradiction a security reviewer opens with, and §9.1's requirement is
            # that no access channel *exists* -- not that the storage layer is tidy.
            "vendor_access": _vendor_access_prose(service, posture),
            "crossing_a_boundary": (
                "Data never crosses. A derived rule never crosses automatically: it inherits the "
                "perimeter of its source data and its evidence is refused outside it. Only a human "
                "carries understanding between capsules (§9.3)."
            ),
        }

    @app.post("/api/cases", status_code=status.HTTP_201_CREATED)
    def create_case(payload: dict[str, Any], perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        perimeter.require_operator(what="A design case")
        raw_request = payload.get("raw_request")
        if raw_request is not None:
            if not isinstance(raw_request, str) or not raw_request.strip():
                raise HTTPException(status_code=422, detail="raw_request must be a non-empty string.")
            case_id = str(uuid.uuid4())
            now = _now()
            with service.session(perimeter) as session:
                session.add(CaseRow(case_id=case_id, perimeter_key=perimeter.key, case_json=None, raw_request=raw_request, created_at=now, updated_at=now))
            next_action = (
                f"POST /api/cases/{case_id}/intake"
                if service.agentic
                else f"PUT /api/cases/{case_id} with a structured Case (agentic intake is disabled)"
            )
            return {"id": case_id, **_perimeter_fields(perimeter), "status": "raw_input_saved", "next_action": next_action}
        case = _normalize_case(payload, perimeter)
        with service.session(perimeter) as session:
            if session.get(CaseRow, case.metadata.case_id) is not None:
                raise HTTPException(status_code=409, detail="case_id already exists.")
            now = _now()
            session.add(CaseRow(case_id=case.metadata.case_id, perimeter_key=perimeter.key, case_json=_json(_serialize_case(case)), raw_request=case.metadata.raw_request, created_at=now, updated_at=now))
        return {"id": case.metadata.case_id, **_perimeter_fields(perimeter), "status": "created", "case": _serialize_case(case)}

    @app.post("/api/cases/{case_id}/intake")
    def run_intake(case_id: str, payload: dict[str, Any] | None = None, perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        perimeter.require_operator(what="Case intake")
        with service.session(perimeter) as session:
            row = _get_case_row(session, perimeter, case_id)
            if not service.agentic:
                # A refusal, not an error: the request was well-formed and the
                # deployment is deliberately configured without this capability,
                # so the caller is told what to do instead.
                return {
                    "id": case_id,
                    **_perimeter_fields(perimeter),
                    "status": "refused_agentic_layer_disabled",
                    "refused": True,
                    "refusal_reason": _INTAKE_DISABLED_REASON,
                    "next_action": f"PUT /api/cases/{case_id}",
                    "intake": None,
                    "case": _load_json(row.case_json),
                }
            raw = (payload or {}).get("raw_request") or row.raw_request
            if not isinstance(raw, str) or not raw.strip():
                raise HTTPException(status_code=422, detail="No raw request is attached to this case.")
            try:
                input_source = Source((payload or {}).get("input_source", Source.TEXT_EXTRACTION.value))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="input_source must be an esp_engine Source value.") from exc
            result = IntakeAgent(service.llm).run(raw, case_id=case_id, tenant_id=perimeter.key, source_documents=(payload or {}).get("source_documents"), input_source=input_source)
            row.raw_request = raw
            row.intake_json = _json(result.model_dump(mode="json", exclude_none=True))
            row.updated_at = _now()
            if result.case is not None:
                row.case_json = _json(_serialize_case(result.case))
            return {"id": case_id, "intake": result.model_dump(mode="json", exclude_none=True), "case": _serialize_case(result.case) if result.case is not None else None}

    @app.get("/api/projects/{project_id}/preferences")
    def get_project_preferences(project_id: str, perimeter: Perimeter=Depends(_perimeter), service:ApiService=Depends(_get_service)):
        perimeter.require_operator(what="Project engineering preferences")
        with service.session(perimeter) as session:
            row=session.get(ProjectPreferenceRow,project_id)
            if row is None: return {"preference":None,**_perimeter_fields(perimeter)}
            _assert_row_perimeter(row,perimeter)
            return {"preference":json.loads(row.preference_json),"confirmed_by":row.confirmed_by,**_perimeter_fields(perimeter)}

    @app.put("/api/projects/{project_id}/preferences")
    def save_project_preferences(project_id: str, payload:dict[str,Any], perimeter:Perimeter=Depends(_perimeter),service:ApiService=Depends(_get_service)):
        perimeter.require_operator(what="Project engineering preferences")
        if payload.get("confirmed") is not True or not str(payload.get("engineer_id","")).strip():
            raise HTTPException(422,"Explicit engineer confirmation and identifier are required; selections never create preferences.")
        if payload.get("ranking_mode") not in {"run_life","bep_target"}:
            raise HTTPException(422,"ranking_mode must be run_life or bep_target")
        data={k:payload.get(k) for k in ("ranking_mode","note","prefer_lower_cost","accept_single_seal")}
        data["confirmed"]=True
        with service.session(perimeter) as session:
            row=session.get(ProjectPreferenceRow,project_id)
            if row is not None: _assert_row_perimeter(row,perimeter)
            else:
                row=ProjectPreferenceRow(project_id=project_id,perimeter_key=perimeter.key)
                session.add(row)
            row.preference_json=_json(data);row.confirmed_by=payload["engineer_id"];row.updated_at=_now()
        return {"preference":data,**_perimeter_fields(perimeter)}

    @app.get("/api/cases/{case_id}")
    def get_case(case_id: str, perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        perimeter.require_operator(what="A design case")
        with service.session(perimeter) as session:
            row = _get_case_row(session, perimeter, case_id)
            return {"id": row.case_id, **_perimeter_fields(perimeter), "case": _load_json(row.case_json), "raw_request": row.raw_request, "intake": _load_json(row.intake_json), "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}

    # PUT and PATCH are the same operation here: both replace the structured Case
    # and record an agreement record. PUT is named in the agentic-off refusal, so
    # the route the refusal points at has to exist.
    @app.put("/api/cases/{case_id}")
    @app.patch("/api/cases/{case_id}")
    def update_case(case_id: str, payload: dict[str, Any], perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        perimeter.require_operator(what="A design case")
        with service.session(perimeter) as session:
            row = _get_case_row(session, perimeter, case_id)
            case = _normalize_case(payload, perimeter, case_id)
            row.case_json = _json(_serialize_case(case))
            row.raw_request = case.metadata.raw_request or row.raw_request
            row.updated_at = _now()
            review = _record_review(session, perimeter=perimeter, case=case, design_id=None, action="override", body={"override_reason": payload.get("override_reason", "Engineer updated Case inputs."), "engineer_id": payload.get("engineer_id"), "final_decision": {"case_update": True}})
            return {"id": case_id, "status": "updated", "case": _serialize_case(case), "agreement_record_id": review.review_id}

    def _submit_job(
        perimeter: Perimeter, service: ApiService, *, kind: str, case_id: str,
        fingerprint: str, design_id: str | None,
    ) -> tuple[JobRow, bool]:
        """Admit and queue a job, or return the identical one already active."""
        with service.session(perimeter) as session:
            query = select(JobRow).where(
                JobRow.case_id == case_id, JobRow.kind == kind,
                JobRow.case_fingerprint == fingerprint, JobRow.status.in_(JOB_ACTIVE),
            )
            if design_id is not None:
                query = query.where(JobRow.design_id == design_id)
            for existing in session.scalars(query.order_by(JobRow.created_at.desc())):
                _assert_row_perimeter(existing, perimeter)
                if service.runner.is_live(existing.job_id):
                    session.expunge(existing)
                    return existing, True
                _reconcile_job(service, existing)
        job_id = str(uuid.uuid4())
        try:
            service.runner.admit(job_id, perimeter.key)
        except AdmissionRefused as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc),
                headers={"Retry-After": str(exc.retry_after_s)},
            ) from exc
        try:
            stage = STAGE_ENGINE if kind == "design" else STAGE_NARRATIVE
            with service.session(perimeter) as session:
                row = JobRow(
                    job_id=job_id, perimeter_key=perimeter.key, case_id=case_id, kind=kind,
                    status="queued", phase=stage, case_fingerprint=fingerprint, design_id=design_id,
                    narrative_status="pending" if kind == "narrative" else None,
                    created_at=_now(),
                )
                session.add(row)
                session.flush()
                session.expunge(row)
            if kind == "design":
                fn = _engine_stage(service, perimeter, job_id, case_id)
            else:
                assert design_id is not None
                _attach_narrative(service, perimeter, design_id, _pending_narrative(job_id))
                fn = _narrative_stage(service, perimeter, job_id, design_id)
            service.runner.submit(job_id, stage, fn)
        except BaseException:
            service.runner.finish(job_id)
            raise
        return row, False

    def _job_response(
        perimeter: Perimeter, service: ApiService, job_id: str, *, deduplicated: bool,
        wait: bool, timeout_s: float,
    ):
        if wait:
            service.runner.wait(job_id, timeout_s)
        with service.session(perimeter) as session:
            row = _reconcile_job(service, _get_job_row(session, perimeter, job_id))
            job = _present_job(row, service)
            if wait and job["terminal"]:
                if job["status"] == "failed" and not job["design_id"]:
                    raise HTTPException(status_code=422, detail=job["error"])
                if job["design_id"]:
                    design = _present_design(_get_design_row(session, perimeter, job["design_id"]), service)
                    return {**design, "job": job}
        body = {**_perimeter_fields(perimeter), "job": job, "deduplicated": deduplicated}
        code = status.HTTP_200_OK if deduplicated or job["terminal"] else status.HTTP_202_ACCEPTED
        return JSONResponse(status_code=code, content=body, headers={"Location": job["links"]["self"]})

    @app.post("/api/cases/{case_id}/design")
    def create_design(
        case_id: str,
        wait: bool = Query(False, description="Block until the run is terminal. For scripts and tests; the UI polls."),
        timeout_s: float = Query(600.0, ge=0.0, le=3600.0),
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ):
        """Queue a design run and return its job (202).

        The deterministic stage stores the design's facts first; the narrative
        attaches later. Poll ``GET /api/jobs/{job_id}``: once ``facts_ready`` is
        true the design is readable. Submitting the same case while an identical
        run is active returns that run (200, ``deduplicated``) instead of
        queueing a second one.
        """
        perimeter.require_operator(what="A design run")
        with service.session(perimeter) as session:
            case_row = _get_case_row(session, perimeter, case_id)
            _case_from_row(case_row)  # 409 now rather than as a failed job later
            fingerprint = _case_fingerprint(service, case_row.case_json or "")
        row, deduplicated = _submit_job(
            perimeter, service, kind="design", case_id=case_id, fingerprint=fingerprint, design_id=None,
        )
        return _job_response(perimeter, service, row.job_id, deduplicated=deduplicated, wait=wait, timeout_s=timeout_s)

    @app.post("/api/designs/{design_id}/narrative")
    def retry_narrative(
        design_id: str,
        wait: bool = Query(False),
        timeout_s: float = Query(600.0, ge=0.0, le=3600.0),
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ):
        """Re-run only the narrative stage for a stored design.

        For a narrative that was refused, cancelled, or interrupted by a
        restart. The facts are not recomputed.
        """
        perimeter.require_operator(what="A design narrative")
        if not service.agentic:
            raise HTTPException(status_code=409, detail=_NARRATIVE_DISABLED_REASON)
        with service.session(perimeter) as session:
            design = _get_design_row(session, perimeter, design_id)
            case_id = design.case_id
            fingerprint = hashlib.sha256(f"narrative:{design_id}".encode()).hexdigest()
        row, deduplicated = _submit_job(
            perimeter, service, kind="narrative", case_id=case_id, fingerprint=fingerprint, design_id=design_id,
        )
        return _job_response(perimeter, service, row.job_id, deduplicated=deduplicated, wait=wait, timeout_s=timeout_s)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        perimeter.require_operator(what="A design run")
        with service.session(perimeter) as session:
            row = _reconcile_job(service, _get_job_row(session, perimeter, job_id))
            return {**_perimeter_fields(perimeter), "job": _present_job(row, service)}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        """Cancel a queued or running job.

        A queued job never starts. A running engine stage stops before it stores
        anything. A running narrative stage makes no further model calls and
        discards any reply already in flight; the stored facts are kept.
        """
        perimeter.require_operator(what="A design run")
        with service.session(perimeter) as session:
            row = _reconcile_job(service, _get_job_row(session, perimeter, job_id))
            if row.status in JOB_TERMINAL:
                raise HTTPException(status_code=409, detail=f"Job is already {row.status}.")
            design_id, phase = row.design_id, row.phase
        was_queued = service.runner.cancel(job_id)
        if was_queued:
            # The stage was removed from its pool, so no stage code will record
            # the outcome; record it here and release the slot.
            if design_id and phase == STAGE_NARRATIVE:
                _attach_narrative(service, perimeter, design_id, _cancelled_narrative(job_id))
            _update_job(service, perimeter, job_id, status="cancelled", phase="done", finished_at=_now(),
                        narrative_status="cancelled" if design_id else None,
                        error="Cancelled while queued; it never started." if not design_id else
                        "Cancelled while the narrative was queued. The design's facts were already stored and are unaffected.")
            service.runner.finish(job_id)
        else:
            # Running: the stage records its own cancellation at its next check.
            service.runner.wait(job_id, 2.0)
        with service.session(perimeter) as session:
            row = _reconcile_job(service, _get_job_row(session, perimeter, job_id))
            return {**_perimeter_fields(perimeter), "job": _present_job(row, service)}

    @app.get("/api/cases/{case_id}/jobs")
    def list_jobs_for_case(
        case_id: str,
        active: bool = Query(False),
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        """Jobs for a case, newest first. ``active=true`` lets a reloaded page resume polling."""
        perimeter.require_operator(what="A design run")
        with service.session(perimeter) as session:
            _get_case_row(session, perimeter, case_id)
            rows = list(session.scalars(
                select(JobRow).where(JobRow.case_id == case_id).order_by(JobRow.created_at.desc()).limit(50)
            ))
            jobs = []
            for row in rows:
                _assert_row_perimeter(row, perimeter)
                _reconcile_job(service, row)
                if active and row.status not in JOB_ACTIVE:
                    continue
                jobs.append(_present_job(row, service))
        return {**_perimeter_fields(perimeter), "case_id": case_id, "count": len(jobs), "jobs": jobs}

    @app.get("/api/cases/{case_id}/designs")
    def list_designs_for_case(
        case_id: str,
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        """Prior design runs for a case, newest first.

        A design run is an immutable, reproducible artifact keyed by
        case_hash + config_hash + catalog_version. Re-running one to display it
        costs minutes of enumeration and produces a byte-identical result, so a
        client that wants to show "the design" should fetch a stored run rather
        than recompute it. Without this endpoint the frontend had no way to find
        an existing run and re-ran the engine on every page load.

        The reproducibility triple is returned per run so a client can tell
        whether a stored run is still valid for the current case, config, and
        catalog, instead of assuming the newest one is current.
        """
        perimeter.require_operator(what="A design run")
        with service.session(perimeter) as session:
            _get_case_row(session, perimeter, case_id)  # 404 on unknown case
            rows = list(
                session.scalars(
                    select(DesignRow)
                    .where(DesignRow.case_id == case_id)
                    .order_by(DesignRow.created_at.desc())
                )
            )
            summaries = []
            for row in rows:
                _assert_row_perimeter(row, perimeter)
                facts = json.loads(row.facts_json)
                summaries.append(
                    {
                        "design_id": row.design_id,
                        "case_id": row.case_id,
                        "created_at": row.created_at.isoformat(),
                        "verdict": facts.get("verdict"),
                        "candidate_count": len(facts.get("candidates") or []),
                        "case_hash": row.case_hash,
                        "config_hash": row.config_hash,
                        "catalog_version": row.catalog_version,
                        "engine_version": row.engine_version,
                    }
                )
        return {
            **_perimeter_fields(perimeter),
            "case_id": case_id,
            "count": len(summaries),
            "designs": summaries,
        }

    @app.get("/api/designs/{design_id}")
    def get_design(design_id: str, perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        perimeter.require_operator(what="A design run")
        with service.session(perimeter) as session:
            return _present_design(_get_design_row(session, perimeter, design_id), service)

    @app.get("/api/designs/{design_id}/report")
    def get_report(design_id: str, perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        perimeter.require_operator(what="A design report")
        with service.session(perimeter) as session:
            presentation = _present_design(_get_design_row(session, perimeter, design_id), service)
        return {"id": design_id, "facts": presentation["facts"], "judgments": presentation["judgments"], "report_markdown": _render_report(presentation)}

    @app.post("/api/designs/{design_id}/review")
    def review_design(design_id: str, payload: dict[str, Any], perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        perimeter.require_operator(what="A design review")
        with service.session(perimeter) as session:
            design = _get_design_row(session, perimeter, design_id)
            case = Case.model_validate(_load_json(design.case_snapshot_json))
            review = _record_review(session, perimeter=perimeter, case=case, design_id=design_id, action=str(payload.get("action", "")), body=payload)
            return {"id": review.review_id, "design_id": design_id, "action": review.action, "agreed": review.agreed, "created_at": review.created_at.isoformat()}

    # Catalog endpoints deliberately do not require an operator. The equipment
    # catalog and internal standards belong to the outer capsule (§9.2) and are
    # shared downward, so an org-level caller must be able to read them.
    @app.get("/api/catalog/synthetic")
    def get_synthetic_catalog(perimeter:Perimeter=Depends(_perimeter)):
        from esp_engine.synthetic import synthetic_catalog
        return {**_perimeter_fields(perimeter), "catalog":synthetic_catalog().model_dump(mode="json"),
            "notice":"SYNTHETIC DEMONSTRATION ONLY. No manufacturer, purchasable part or field rating is represented."}

    @app.get("/api/catalog")
    def get_catalog(
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        return {
            **_perimeter_fields(perimeter),
            "version": service.catalog.version,
            "has_estimated_data": service.catalog.has_estimated_data,
            "counts": {
                "pumps": len(service.catalog.pumps),
                "motors": len(service.catalog.motors),
                "cables": len(service.catalog.cables),
                "gas_handling": len(service.catalog.gas_handling),
                "seals": len(service.catalog.seals),
                "casing": len(service.catalog.casing),
            },
        }

    def _catalog_records(
        records: list[Any], *, catalog_version: str, perimeter: Perimeter
    ) -> dict[str, Any]:
        """Echo the caller's perimeter even though catalog data is org-level."""
        return {
            **_perimeter_fields(perimeter),
            "catalog_version": catalog_version,
            "items": [record.model_dump(mode="json") for record in records],
        }

    @app.get("/api/catalog/pumps")
    def catalog_pumps(
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        return _catalog_records(
            service.catalog.pumps,
            catalog_version=service.catalog.version,
            perimeter=perimeter,
        )

    @app.get("/api/catalog/motors")
    def catalog_motors(
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        return _catalog_records(
            service.catalog.motors,
            catalog_version=service.catalog.version,
            perimeter=perimeter,
        )

    @app.get("/api/catalog/cables")
    def catalog_cables(
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        return _catalog_records(
            service.catalog.cables,
            catalog_version=service.catalog.version,
            perimeter=perimeter,
        )

    @app.get("/api/catalog/gas-handling")
    def catalog_gas_handling(
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        return _catalog_records(
            service.catalog.gas_handling,
            catalog_version=service.catalog.version,
            perimeter=perimeter,
        )

    @app.get("/api/catalog/seals")
    def catalog_seals(
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        return _catalog_records(
            service.catalog.seals,
            catalog_version=service.catalog.version,
            perimeter=perimeter,
        )

    @app.get("/api/catalog/casing")
    def catalog_casing(
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        return _catalog_records(
            service.catalog.casing,
            catalog_version=service.catalog.version,
            perimeter=perimeter,
        )

    @app.get("/api/catalog/pumps/{pump_id}/curve")
    def catalog_pump_curve(
        pump_id: str,
        frequency_hz: float = 60.0,
        stages: int = 1,
        samples: int = 60,
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        """Sampled head/efficiency/BHP curve for one pump at one frequency.

        The design-results view traces the operating point's path over time on the
        pump curve, which requires the curve itself. Without this the frontend can
        only draw the path against an implied background, so the chart that makes
        "when will this stop working" legible loses the reference it is read
        against. Zone boundaries are returned alongside so the thrust bands are
        shaded from engine values rather than re-derived in JavaScript.
        """
        if samples < 2 or samples > 400:
            raise HTTPException(status_code=422, detail="samples must be between 2 and 400")
        if stages < 1:
            raise HTTPException(status_code=422, detail="stages must be at least 1")
        try:
            from esp_engine.synthetic import synthetic_catalog
            pump = (synthetic_catalog() if pump_id.startswith("synthetic-") else service.catalog).pump(pump_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            # Use the engine's configured frequency-scaling method rather than a
            # local default, so a chart cannot be drawn by a different method than
            # the design it illustrates.
            curve = scale_curve_to_frequency(
                pump, frequency_hz, service.engine_config.correlations.frequency_curves
            )
        except ValueError as exc:
            # An out-of-range frequency is a client error, not a server fault.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        q_min, q_max = curve_domain(curve)
        step = (q_max - q_min) / (samples - 1)
        points: list[dict[str, Any]] = []
        for index in range(samples):
            q_bpd = q_min + step * index
            evaluated = try_evaluate_stage(curve, q_bpd)
            if evaluated is None:
                continue
            points.append(
                {
                    "q_bpd": evaluated.q_bpd,
                    "head_ft_per_stage": evaluated.head_ft_per_stage,
                    "head_ft_total": evaluated.head_ft_per_stage * stages,
                    "efficiency_frac": evaluated.efficiency_frac,
                    "bhp_per_stage": evaluated.bhp_per_stage,
                    "bhp_total": evaluated.bhp_per_stage * stages,
                }
            )

        return {
            **_perimeter_fields(perimeter),
            "pump_id": curve.pump_id,
            "pump_model": pump.model,
            "manufacturer": pump.manufacturer,
            "catalog_version": service.catalog.version,
            "frequency_hz": curve.frequency_hz,
            "reference_frequency_hz": curve.reference_frequency_hz,
            "stages": stages,
            "bep_q_bpd": curve.bep_q_bpd,
            "q_min_bpd": curve.q_min_bpd,
            "q_max_bpd": curve.q_max_bpd,
            "recommended_min_bpd": curve.recommended_min_bpd,
            "recommended_max_bpd": curve.recommended_max_bpd,
            "downthrust_limit_bpd": curve.downthrust_limit_bpd,
            "upthrust_limit_bpd": curve.upthrust_limit_bpd,
            # Surfaced explicitly: every seeded curve is a parametric estimate, so
            # a chart drawn from it must not be presented as a vendor curve.
            # Surfaced from the catalog's own quality field rather than assumed, so
            # a future digitized vendor curve reports itself correctly.
            "data_quality": pump.data_quality,
            "is_estimated": is_estimated_record(pump),
            "frequency_curve_method": service.engine_config.correlations.frequency_curves.value,
            "caveats": _curve_caveats(points, is_estimated=is_estimated_record(pump)),
            "points": points,
        }

    @app.get("/api/empirical/survival")
    def empirical_survival(
        pump_model: str | None = None,
        confidence_level: float = 0.95,
        perimeter: Perimeter = Depends(_perimeter),
        service: ApiService = Depends(_get_service),
    ) -> dict[str, Any]:
        """Kaplan-Meier run-life survival from this perimeter's own observations.

        Returned as a first-class endpoint so the empirical view plots real
        censored field history instead of a fixture. An empty perimeter yields an
        explicit empty estimate rather than a synthetic curve: showing an invented
        survival band next to real ones would be the single most misleading thing
        this product could do.
        """
        perimeter.require_operator(what="Run-life history")
        if not 0.0 < confidence_level < 1.0:
            raise HTTPException(
                status_code=422, detail="confidence_level must be between 0 and 1"
            )
        with service.empirical(perimeter) as repository:
            observations = repository.list_observations(pump_model=pump_model)

        run_lives = [
            RunLifeObservation(
                observation_id=observation.observation_id,
                run_life_days=float(
                    (observation.outcome_observed_date - observation.install_date).days
                ),
                event_occurred=observation.is_failure,
            )
            for observation in observations
        ]

        if not run_lives:
            return {
                **_perimeter_fields(perimeter),
                "pump_model": pump_model,
                "n_observations": 0,
                "estimate": None,
                "explanation": (
                    "No observations recorded in this perimeter, so no survival "
                    "estimate can be computed. This is an absence of evidence, not "
                    "evidence of long run life."
                ),
            }

        estimate = kaplan_meier(run_lives, confidence_level=confidence_level)
        return {
            **_perimeter_fields(perimeter),
            "pump_model": pump_model,
            "n_observations": len(run_lives),
            "estimate": estimate.model_dump(mode="json"),
        }

    @app.get("/api/empirical/rules")
    def list_empirical_rules(perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        perimeter.require_operator(what="Derived empirical rules")
        with service.empirical(perimeter) as repository:
            rules = repository.list_derived_rules()
        presented = [_present_rule(rule, perimeter) for rule in rules]
        return {
            **_perimeter_fields(perimeter),
            "rules": presented,
            "evidence_refusals": [
                rule["evidence_withheld_reason"] for rule in presented if rule["evidence_withheld"]
            ],
        }

    @app.post("/api/empirical/observations", status_code=status.HTTP_201_CREATED)
    def add_empirical_observation(payload: dict[str, Any], perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        perimeter.require_operator(what="A field observation")
        try:
            draft = ObservationDraft.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc
        with service.empirical(perimeter) as repository:
            observation = repository.add_observation(draft)
        return observation.model_dump(mode="json")

    @app.get("/api/trust")
    def get_trust(perimeter: Perimeter = Depends(_perimeter), service: ApiService = Depends(_get_service)) -> dict[str, Any]:
        perimeter.require_operator(what="Agreement history")
        with service.session(perimeter) as session:
            records = list(session.scalars(select(ReviewRow)))
            for record in records:
                _assert_row_perimeter(record, perimeter)
        buckets: dict[tuple[str, str, str], list[ReviewRow]] = {}
        for record in records:
            key = (record.branch or "unknown", record.complication_segment or "standard", record.confidence_band or "unknown")
            buckets.setdefault(key, []).append(record)
        segments = []
        for (branch, complication, confidence), rows in sorted(buckets.items()):
            accepted = sum(row.agreed for row in rows)
            rate = accepted / len(rows)
            level = TrustLevel.L3 if len(rows) >= 50 and rate >= 0.95 else TrustLevel.L2 if len(rows) >= 20 and rate >= 0.80 else TrustLevel.L1
            segments.append({"branch": branch, "complication_segment": complication, "confidence_band": confidence, "trust_level": level.value, "agreement_count": len(rows), "accepted_count": accepted, "agreement_rate": rate})
        return {**_perimeter_fields(perimeter), "segments": segments, "policy": "L2 requires >=20 reviews and >=80% agreement; L3 requires >=50 reviews and >=95% agreement."}

    return app


# Importable ASGI application for uvicorn and FastAPI TestClient.
app = create_app()


def _demo_assumption(value: float, unit: str, *, basis: str, rationale: str, bias: BiasDirection = BiasDirection.NEUTRAL, unbiased: float | None = None, swept: bool = False) -> Tracked[float]:
    return Tracked.assumed(value, Assumption(basis=basis, rationale=rationale, bias=bias, unbiased_value=unbiased, scenario_swept=swept), unit)


def _demo_geometry(depth: float = 9000.0) -> WellGeometry:
    return WellGeometry(
        casing_sections=[CasingSection(od_in=7.0, id_in=6.276, drift_id_in=6.246, top_md_ft=0.0, bottom_md_ft=9500.0)],
        deviation_survey=[DeviationSurveyPoint(md_ft=0.0, tvd_ft=0.0, inclination_deg=0.0, dogleg_severity_deg_per_100ft=0.0), DeviationSurveyPoint(md_ft=4000.0, tvd_ft=3960.0, inclination_deg=12.0, dogleg_severity_deg_per_100ft=1.5), DeviationSurveyPoint(md_ft=7500.0, tvd_ft=7180.0, inclination_deg=18.0, dogleg_severity_deg_per_100ft=1.2), DeviationSurveyPoint(md_ft=depth, tvd_ft=depth-1600.0, inclination_deg=20.0, dogleg_severity_deg_per_100ft=0.8)],
        perforation_top_md_ft=Tracked.measured(depth+300.0, "ft", source=Source.MEASUREMENT, note="completion report"),
        total_depth_md_ft=Tracked.measured(depth+800.0, "ft", source=Source.MEASUREMENT, note="completion report"),
        tubing_id_in=Tracked.measured(2.441, "in", source=Source.MEASUREMENT, note="completion report"),
    )


def _demo_feasible_case() -> Case:
    return Case(
        metadata=CaseMetadata(case_id="demo-permian-h12", tenant_id=DEMO_PERIMETER.key, well_name="Permian H-12", field_name="Demo Wolfcamp", operator="Demo Operator", region="Permian Basin", raw_request="Seeded demonstration case; inputs marked with source and assumptions."),
        task_type=TaskType.NEW_WELL,
        expectations=Expectations(target_rate_bpd=Tracked.stated(2000.0, "bpd", note="customer design target"), wellhead_pressure_psi=Tracked.stated(200.0, "psi", note="customer surface requirement"), design_life_months=Tracked.stated(24.0, "months", note="customer planning horizon")),
        constraints=Constraints(electrical=ElectricalConstraints(vsd_available=Tracked.stated(True, None, note="site VSD inventory"), frequency_min_hz=Tracked.stated(45.0, "Hz", note="VSD nameplate"), frequency_max_hz=Tracked.stated(65.0, "Hz", note="VSD nameplate"), available_surface_voltage_v=Tracked.stated(4160.0, "V", note="site electrical survey"))),
        geometry=_demo_geometry(),
        fluid=FluidProperties(oil_api=Tracked.measured(34.0, "API", source=Source.MEASUREMENT, note="PVT report"), gas_sg=Tracked.measured(0.78, None, source=Source.MEASUREMENT, note="PVT report"), water_cut_frac=Tracked.stated(0.35, "fraction", note="offset-well forecast"), gor_scf_stb=Tracked.stated(450.0, "scf/stb", note="offset-well forecast"), water_salinity_ppm=_demo_assumption(35000.0, "ppm", basis="Permian produced-water regional typical", rationale="No water analysis was supplied; assumption is explicitly disclosed.")),
        reservoir=ReservoirProperties(reservoir_pressure_psi=Tracked.measured(3200.0, "psi", source=Source.MEASUREMENT, note="buildup test"), bht_f=Tracked.measured(185.0, "F", source=Source.MEASUREMENT, note="temperature log"), surface_temp_f=_demo_assumption(80.0, "F", basis="Permian surface operating typical", rationale="No surface temperature was supplied."), productivity_index_bpd_psi=Tracked.measured(1.6, "bpd/psi", source=Source.MEASUREMENT, note="buildup test"), bubble_point_psi=Tracked.measured(1900.0, "psi", source=Source.MEASUREMENT, note="PVT report")),
        trajectory=TrajectorySpec(horizon_months=24.0),
    )


def _demo_missing_data_case() -> Case:
    return Case(metadata=CaseMetadata(case_id="demo-intake-blocked", tenant_id=DEMO_PERIMETER.key, well_name="Delaware 17-4", field_name="Demo Delaware", region="Permian Basin", raw_request="Customer requested an ESP review but did not provide rate, casing program, or survey."), task_type=TaskType.NEW_WELL, expectations=Expectations(design_life_months=Tracked.stated(18.0, "months", note="customer planning horizon")), fluid=FluidProperties(gor_scf_stb=_demo_assumption(800.0, "scf/stb", basis="gassy Delaware regional typical", rationale="No GOR measurement supplied; biased upward to protect gas handling selection.", bias=BiasDirection.UPWARD, unbiased=550.0)), reservoir=ReservoirProperties(bht_f=_demo_assumption(210.0, "F", basis="regional temperature gradient", rationale="No bottomhole temperature supplied; biased upward for motor/cable derating.", bias=BiasDirection.UPWARD, unbiased=200.0)), trajectory=TrajectorySpec(horizon_months=18.0))


def _demo_unachievable_case() -> Case:
    base = _demo_feasible_case()
    return base.model_copy(update={"metadata": base.metadata.model_copy(update={"case_id": "demo-target-unachievable", "well_name": "Permian H-12 High Target"}), "expectations": base.expectations.model_copy(update={"target_rate_bpd": Tracked.stated(90000.0, "bpd", note="deliberately aggressive customer target for refusal demo")})})
