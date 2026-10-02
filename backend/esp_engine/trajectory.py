"""Scenario expansion — the time axis of the design.

Framework refs: §6 (time trajectory instead of point calculation), §6.1 (what
drifts: PI, reservoir pressure, water cut, GOR), §6.2 (human practice is
discrete cases; the limitation is time and software instability, not
methodology), §6.3 (target: a continuous range from minimum to maximum).

Framework §6.2 is the key insight: engineers already know they should check
day 1 / +2 mo / +6 mo / +1 yr. They run 3-4 cases instead of 20 because the
software is slow and loses work — not because the methodology is wrong. So this
module is not introducing a new method; it is removing the constraint that
prevented the existing method from being applied properly.

Drift models are deliberately simple (exponential/harmonic decline, logistic
water cut). Reservoir-simulation-grade forecasting is out of v0.1 scope. The
value is in *sweeping* the trajectory at all, and in provenancing where each
drift model came from — a decline-curve fit on real history (Branch A) is a very
different object from an offset-well analog (Branch B), and the report must say
which it is.
"""

from __future__ import annotations

import math

from .models import Case, DriftModel, TaskBranch
from .provenance import Source, Tracked
from .results import EnvelopeCase, ScenarioPoint

# =============================================================================
# Drift evaluation
# =============================================================================


def evaluate_drift(model: DriftModel, initial_value: float, months: float) -> float:
    """Value of a drifting parameter at ``months`` from now.

    Forms:
        constant     — no change
        linear       — v0 + rate * years
        exponential  — v0 * exp(-rate * years)     (classical decline)
        harmonic     — v0 / (1 + rate * years)     (slower late-life decline)
        logistic     — approaches terminal_value asymptotically (water cut)

    ``rate_per_year`` is a fractional decline rate for exponential/harmonic and
    an absolute change per year for linear.
    """
    years = months / 12.0
    rate = model.rate_per_year

    if model.form == "constant" or rate == 0.0:
        value = initial_value

    elif model.form == "linear":
        value = initial_value + rate * years

    elif model.form == "exponential":
        value = initial_value * math.exp(-rate * years)

    elif model.form == "harmonic":
        denom = 1.0 + rate * years
        value = initial_value / denom if denom > 1e-9 else initial_value

    elif model.form == "logistic":
        # Approaches terminal_value with a time constant of 1/rate years.
        terminal = (
            model.terminal_value if model.terminal_value is not None else initial_value
        )
        value = terminal + (initial_value - terminal) * math.exp(-rate * years)

    else:  # pragma: no cover - guarded by the Literal type
        raise ValueError(f"unknown drift form: {model.form}")

    # Clamp to the terminal value for monotone forms, so an aggressive rate can
    # never overshoot a physical ceiling (e.g. water cut above 1.0).
    if model.terminal_value is not None and model.form != "logistic":
        if initial_value <= model.terminal_value:
            value = min(value, model.terminal_value)
        else:
            value = max(value, model.terminal_value)

    return value


def scale_drift_for_envelope(
    model: DriftModel, envelope: EnvelopeCase
) -> DriftModel:
    """Perturb a drift rate to build the min/max envelope (§6.3).

    ``min`` = the pessimistic case for the *well* (faster decline, faster
    watering out, more gas), ``max`` = the optimistic case. Note the asymmetry
    this creates in scoring: a config must survive BOTH ends, because a design
    that only works in the base case is not a design, it is a guess with error
    bars hidden.
    """
    if envelope is EnvelopeCase.BASE:
        return model
    factor = 1.0 + (
        model.uncertainty_frac if envelope is EnvelopeCase.MIN else -model.uncertainty_frac
    )
    return model.model_copy(update={"rate_per_year": model.rate_per_year * factor})


# =============================================================================
# Default drift models
# =============================================================================


def default_drift_models(case: Case) -> dict[str, DriftModel]:
    """Sensible defaults when the case does not supply drift models.

    Branch A (anchored) gets tighter uncertainty because there is measured
    history to lean on; Branch B gets wide uncertainty because there is not
    (§4). This is the single most honest thing the trajectory layer does: an
    unanchored forecast presented with narrow error bars is a lie, and the
    framework explicitly requires Branch B results to be delivered as a range.
    """
    is_anchored = case.branch is TaskBranch.A_REPLACEMENT
    uncertainty = 0.25 if is_anchored else 0.55
    basis = (
        "decline behaviour of the previous installation on this well"
        if is_anchored
        else "offset-well analog / regional typical (no anchor available)"
    )
    prov = Tracked(
        value=basis,
        source=Source.REPORT if is_anchored else Source.ASSUMPTION,
        confidence=0.6 if is_anchored else 0.3,
    )

    wc_initial = (
        case.fluid.water_cut_frac.value if case.fluid.water_cut_frac else 0.3
    )

    return {
        # §6.1 — productivity index declines as the reservoir depletes and skin
        # develops. Harmonic is the usual best fit for late-life behaviour.
        "productivity_index_bpd_psi": DriftModel(
            parameter="productivity_index_bpd_psi",
            form="harmonic",
            rate_per_year=0.15,
            provenance=prov,
            uncertainty_frac=uncertainty,
        ),
        # §6.1 — reservoir pressure depletion.
        "reservoir_pressure_psi": DriftModel(
            parameter="reservoir_pressure_psi",
            form="exponential",
            rate_per_year=0.08,
            provenance=prov,
            uncertainty_frac=uncertainty,
        ),
        # §6.1 / §5.2 — water cut rises toward a ceiling. Logistic, not linear:
        # linear water cut will exceed 1.0 on any long horizon, which produces
        # silently nonsensical fluid properties.
        "water_cut_frac": DriftModel(
            parameter="water_cut_frac",
            form="logistic",
            rate_per_year=0.35,
            terminal_value=min(0.95, max(0.6, wc_initial + 0.35)),
            provenance=prov,
            uncertainty_frac=uncertainty,
        ),
        # §6.1 — GOR typically rises as pressure falls below bubble point and
        # free gas is produced.
        "gor_scf_stb": DriftModel(
            parameter="gor_scf_stb",
            form="linear",
            rate_per_year=0.0,
            provenance=prov,
            uncertainty_frac=uncertainty,
        ),
    }


def gor_rise_drift(initial_gor: float, reservoir_p: float, bubble_point: float,
                   uncertainty: float, prov: Tracked | None) -> DriftModel:
    """GOR drift, conditioned on proximity to the bubble point.

    Above bubble point, GOR is flat until pressure crosses it. Below, free gas
    liberates in the reservoir and produced GOR climbs — which is precisely the
    regime where an ESP gets into trouble. Framework §3.3 flags gas as the
    critical complication, so the trajectory must model its increase rather than
    hold it constant.
    """
    below_bp = reservoir_p <= bubble_point * 1.1
    return DriftModel(
        parameter="gor_scf_stb",
        form="linear",
        rate_per_year=initial_gor * 0.18 if below_bp else 0.0,
        provenance=prov,
        uncertainty_frac=uncertainty * (1.4 if below_bp else 1.0),
    )


# =============================================================================
# Scenario expansion
# =============================================================================


def _initial_values(case: Case) -> dict[str, float]:
    """Starting values for the drifting parameters, with defensible fallbacks."""
    res = case.reservoir
    fluid = case.fluid

    reservoir_p = (
        res.reservoir_pressure_psi.value if res.reservoir_pressure_psi else None
    )
    pi = (
        res.productivity_index_bpd_psi.value
        if res.productivity_index_bpd_psi
        else None
    )
    wc = fluid.water_cut_frac.value if fluid.water_cut_frac else 0.3
    gor = fluid.gor_scf_stb.value if fluid.gor_scf_stb else 0.0
    bht = res.bht_f.value if res.bht_f else 180.0

    if reservoir_p is None:
        # §5.2: reservoir pressure is "almost never provided". Estimate from a
        # normal hydrostatic gradient at datum. Crude, and flagged as such by the
        # assumption ledger — but a missing reservoir pressure must not block a
        # design when the framework explicitly classes it as a SOFT input.
        datum = (
            res.datum_depth_ft.value
            if res.datum_depth_ft
            else (
                case.geometry.total_depth_md_ft.value
                if case.geometry.total_depth_md_ft
                else 8000.0
            )
        )
        reservoir_p = datum * 0.433

    if pi is None:
        # Back out PI from the previous installation's test point if we have one
        # (Branch A), else use a mid-range value that the scenario sweep will
        # bracket generously.
        pi = _pi_from_reference(case, reservoir_p) or 1.0

    return {
        "reservoir_pressure_psi": reservoir_p,
        "productivity_index_bpd_psi": pi,
        "water_cut_frac": wc,
        "gor_scf_stb": gor,
        "bht_f": bht,
    }


def _pi_from_reference(case: Case, reservoir_p: float) -> float | None:
    """Estimate PI from the previous installation's test point (Branch A anchor).

    Framework §3.4/§4: the previous installation's *actual performance* is the
    anchor. A test point of (rate, PIP) plus a setting depth gives a defensible
    PI, which is far better than a regional guess.
    """
    ref = case.reference
    if ref is None or not ref.test_points:
        return None
    tp = ref.test_points[0]
    if tp.rate_bpd is None or tp.pip_psi is None:
        return None
    rate = tp.rate_bpd.value
    pip = tp.pip_psi.value
    if rate <= 0:
        return None

    # Pwf at the perforations, estimated from PIP plus the gradient over the
    # interval between the pump and the perforations.
    set_depth = (
        ref.setting_depth_md_ft.value
        if ref.setting_depth_md_ft
        else (
            case.expectations.setting_depth_md_ft.value
            if case.expectations.setting_depth_md_ft
            else None
        )
    )
    perf = (
        case.geometry.perforation_top_md_ft.value
        if case.geometry.perforation_top_md_ft
        else None
    )
    if set_depth is None or perf is None or perf <= set_depth:
        pwf = pip
    else:
        interval_tvd = case.geometry.tvd_at_md(perf) - case.geometry.tvd_at_md(set_depth)
        pwf = pip + interval_tvd * 0.40  # mixed-gradient approximation

    drawdown = reservoir_p - pwf
    if drawdown <= 1.0:
        return None
    return rate / drawdown


def build_trajectory(case: Case) -> list[ScenarioPoint]:
    """Expand a case into scenario points across the design horizon.

    Framework §6.3: a continuous range from minimum to maximum scenario. The
    output is the time axis every candidate configuration is scored against.

    Cost note: the returned points are configuration-independent, which is what
    lets the engine solve PVT once per point and reuse it across ~1,200
    candidate configs. That caching turns the expensive part of the run from
    ``configs x points`` into ``points``.
    """
    spec = case.trajectory
    if case.expectations.design_life_months is not None:
        horizon = case.expectations.design_life_months.value
    else:
        horizon = spec.horizon_months

    initial = _initial_values(case)
    drifts = dict(default_drift_models(case))
    drifts.update(spec.drift_models)  # case-supplied models win

    # Condition the GOR drift on bubble-point proximity.
    bp = (
        case.reservoir.bubble_point_psi.value
        if case.reservoir.bubble_point_psi
        else (
            case.fluid.bubble_point_psi.value
            if case.fluid.bubble_point_psi
            else initial["reservoir_pressure_psi"] * 0.6
        )
    )
    if "gor_scf_stb" not in spec.drift_models and initial["gor_scf_stb"] > 0:
        base_gor_model = drifts["gor_scf_stb"]
        drifts["gor_scf_stb"] = gor_rise_drift(
            initial["gor_scf_stb"],
            initial["reservoir_pressure_psi"],
            bp,
            base_gor_model.uncertainty_frac,
            base_gor_model.provenance,
        )

    months_list = (
        [round(i * horizon / (spec.n_points - 1), 4) for i in range(spec.n_points)]
        if spec.n_points > 1
        else [0.0]
    )

    envelopes = (
        [EnvelopeCase.MIN, EnvelopeCase.BASE, EnvelopeCase.MAX]
        if spec.build_envelope
        else [EnvelopeCase.BASE]
    )

    target_rate = (
        case.expectations.target_rate_bpd.value
        if case.expectations.target_rate_bpd
        else 0.0
    )

    points: list[ScenarioPoint] = []
    for envelope in envelopes:
        env_drifts = {
            k: scale_drift_for_envelope(v, envelope) for k, v in drifts.items()
        }
        for month in months_list:
            pi = evaluate_drift(
                env_drifts["productivity_index_bpd_psi"],
                initial["productivity_index_bpd_psi"],
                month,
            )
            res_p = evaluate_drift(
                env_drifts["reservoir_pressure_psi"],
                initial["reservoir_pressure_psi"],
                month,
            )
            wc = evaluate_drift(
                env_drifts["water_cut_frac"], initial["water_cut_frac"], month
            )
            wc = min(max(wc, 0.0), 0.999)
            gor = max(
                0.0,
                evaluate_drift(
                    env_drifts["gor_scf_stb"], initial["gor_scf_stb"], month
                ),
            )

            # Achievable rate: what the reservoir can deliver at a design
            # drawdown of ~60% of reservoir pressure. Deliberately a screening
            # estimate — the real Pwf/PIP solve happens per-cell in the engine,
            # where the pump is known. This value exists so the trajectory can
            # flag rate shortfall before any pump is selected.
            achievable = min(target_rate, pi * res_p * 0.6) if pi > 0 else target_rate

            points.append(
                ScenarioPoint(
                    point_id=f"{envelope.value}_m{month:g}",
                    month=month,
                    envelope=envelope,
                    reservoir_pressure_psi=res_p,
                    productivity_index_bpd_psi=pi,
                    water_cut_frac=wc,
                    gor_scf_stb=gor,
                    bht_f=initial["bht_f"],
                    achievable_rate_bpd=achievable,
                    target_rate_bpd=target_rate,
                    drift_note=_drift_note(month, envelope, wc, res_p, initial),
                )
            )
    return points


def _drift_note(
    month: float,
    envelope: EnvelopeCase,
    wc: float,
    res_p: float,
    initial: dict[str, float],
) -> str | None:
    if month == 0.0:
        return f"initial conditions ({envelope.value} envelope)"
    wc0 = initial["water_cut_frac"]
    p0 = initial["reservoir_pressure_psi"]
    parts = []
    if abs(wc - wc0) > 0.01:
        parts.append(f"water cut {wc0:.0%} -> {wc:.0%}")
    if abs(res_p - p0) > 10:
        parts.append(f"reservoir pressure {p0:.0f} -> {res_p:.0f} psi")
    return "; ".join(parts) if parts else None


def label_for_month(month: float) -> str:
    """Report labels matching framework §6.4's target format."""
    if month <= 0.02:
        return "Day 1"
    if month < 1.0:
        return f"+{round(month * 30.4375):g} d"
    if month < 12.0:
        return f"+{month:g} mo"
    years = month / 12.0
    if abs(years - round(years)) < 0.05:
        return f"+{round(years):g} yr"
    return f"+{years:.1f} yr"
