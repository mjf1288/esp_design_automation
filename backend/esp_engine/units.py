"""Unit constants and conversions for ESP design calculations.

US oilfield field units are the canonical internal representation:

    rate            bpd     (barrels per day, stock tank)
    depth / head    ft
    pressure        psi
    temperature     degF
    power           hp
    voltage         V
    current         A
    density         lb/ft3
    viscosity       cp
    GOR             scf/stb
    diameter        in

There is no dimensioned-quantity wrapper here on purpose. Every function in the
engine names its units in the parameter name (``rate_bpd``, ``depth_ft``) which
in practice catches unit errors far more reliably than a units library that
engineers route around. The guards below catch the specific traps that actually
bite in ESP work.
"""

from __future__ import annotations

# --- Fundamental conversions -------------------------------------------------

FT3_PER_BBL = 5.614583
BBL_PER_FT3 = 1.0 / FT3_PER_BBL

PSI_PER_FT_WATER = 0.4335  # fresh water, 1.0 SG, 60 degF
FT_WATER_PER_PSI = 1.0 / PSI_PER_FT_WATER

WATER_DENSITY_LB_FT3 = 62.4
AIR_MOLECULAR_WEIGHT = 28.9625

SEC_PER_DAY = 86400.0
DAYS_PER_MONTH = 30.4375  # mean Gregorian month
DAYS_PER_YEAR = 365.25

RANKINE_OFFSET = 459.67

HP_PER_KW = 1.34102
KW_PER_HP = 1.0 / HP_PER_KW

# Three-phase power: kVA = sqrt(3) * V * A / 1000
SQRT3 = 1.7320508075688772


# --- Temperature -------------------------------------------------------------


def f_to_r(temp_f: float) -> float:
    """Fahrenheit to Rankine. Absolute temperature is required by every gas law."""
    return temp_f + RANKINE_OFFSET


def r_to_f(temp_r: float) -> float:
    return temp_r - RANKINE_OFFSET


def f_to_c(temp_f: float) -> float:
    return (temp_f - 32.0) * 5.0 / 9.0


def c_to_f(temp_c: float) -> float:
    return temp_c * 9.0 / 5.0 + 32.0


# --- Gravity / density -------------------------------------------------------


def api_to_sg(api_gravity: float) -> float:
    """Oil API gravity to specific gravity (water = 1.0).

    SG = 141.5 / (131.5 + API)
    """
    if api_gravity <= -131.5:
        raise ValueError(f"API gravity {api_gravity} is non-physical (<= -131.5)")
    return 141.5 / (131.5 + api_gravity)


def sg_to_api(sg: float) -> float:
    if sg <= 0:
        raise ValueError(f"specific gravity must be positive, got {sg}")
    return 141.5 / sg - 131.5


def sg_to_gradient_psi_per_ft(sg: float) -> float:
    """Specific gravity to hydrostatic pressure gradient in psi/ft."""
    return sg * PSI_PER_FT_WATER


def gradient_psi_per_ft_to_sg(gradient: float) -> float:
    return gradient / PSI_PER_FT_WATER


def sg_to_density_lb_ft3(sg: float) -> float:
    return sg * WATER_DENSITY_LB_FT3


def density_lb_ft3_to_sg(density: float) -> float:
    return density / WATER_DENSITY_LB_FT3


# --- Head / pressure ---------------------------------------------------------


def psi_to_head_ft(pressure_psi: float, sg: float) -> float:
    """Convert a pressure to the equivalent head of a fluid of given SG.

    head_ft = psi * 2.31 / SG      (2.31 = 1 / 0.4335)

    This is the single most common source of error in ESP hand calculations:
    head is a property of the *pump*, pressure is a property of the *fluid*.
    A pump develops the same head regardless of fluid density; the pressure it
    develops scales with density. Always convert explicitly.
    """
    if sg <= 0:
        raise ValueError(f"specific gravity must be positive, got {sg}")
    return pressure_psi * FT_WATER_PER_PSI / sg


def head_ft_to_psi(head_ft: float, sg: float) -> float:
    return head_ft * sg * PSI_PER_FT_WATER


# --- Rate --------------------------------------------------------------------


def bpd_to_ft3_per_day(rate_bpd: float) -> float:
    return rate_bpd * FT3_PER_BBL


def ft3_per_day_to_bpd(rate_ft3d: float) -> float:
    return rate_ft3d * BBL_PER_FT3


def bpd_to_ft_per_sec(rate_bpd: float, flow_area_ft2: float) -> float:
    """Superficial velocity from volumetric rate and flow area.

    Used for motor cooling velocity checks and tubing friction.
    """
    if flow_area_ft2 <= 0:
        raise ValueError(f"flow area must be positive, got {flow_area_ft2}")
    return bpd_to_ft3_per_day(rate_bpd) / flow_area_ft2 / SEC_PER_DAY


# --- Geometry ----------------------------------------------------------------


def pipe_area_ft2(id_in: float) -> float:
    """Internal cross-sectional area of a circular pipe, ft2, from ID in inches."""
    if id_in <= 0:
        raise ValueError(f"pipe ID must be positive, got {id_in}")
    radius_ft = (id_in / 2.0) / 12.0
    return 3.141592653589793 * radius_ft**2


def annulus_area_ft2(outer_id_in: float, inner_od_in: float) -> float:
    """Annular flow area between a casing ID and an equipment OD, ft2."""
    if outer_id_in <= inner_od_in:
        raise ValueError(
            f"casing ID ({outer_id_in} in) must exceed equipment OD ({inner_od_in} in)"
        )
    return pipe_area_ft2(outer_id_in) - pipe_area_ft2(inner_od_in)


# --- Electrical --------------------------------------------------------------


def three_phase_kva(volts: float, amps: float) -> float:
    return SQRT3 * volts * amps / 1000.0


def three_phase_kw(volts: float, amps: float, power_factor: float) -> float:
    return three_phase_kva(volts, amps) * power_factor


# --- Time --------------------------------------------------------------------


def months_to_days(months: float) -> float:
    return months * DAYS_PER_MONTH


def days_to_months(days: float) -> float:
    return days / DAYS_PER_MONTH


def years_to_days(years: float) -> float:
    return years * DAYS_PER_YEAR


# --- Guards ------------------------------------------------------------------


def require_absolute_temperature(temp_r: float) -> float:
    """Guard against a Fahrenheit value reaching a correlation that needs Rankine.

    Every PVT correlation in this engine takes Rankine. Passing degF silently
    produces plausible-looking but wrong Z-factors and bubble points, which is
    exactly the class of error that must never reach a design.
    """
    if temp_r < 300.0:
        raise ValueError(
            f"temperature {temp_r} looks like degF, not Rankine "
            f"(expected > 300 R). Use units.f_to_r() first."
        )
    return temp_r


def require_positive(name: str, value: float) -> float:
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def require_fraction(name: str, value: float) -> float:
    """Guard against a percentage reaching a function that expects a fraction.

    Water cut is the repeat offender: 85 vs 0.85.
    """
    if not 0.0 <= value <= 1.0:
        raise ValueError(
            f"{name} must be a fraction in [0, 1], got {value}. "
            f"If this is a percentage, divide by 100."
        )
    return value
