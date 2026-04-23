"""
physics.py — pure physics functions for cavity-side condensation analysis.

No I/O, no API calls. Just math. This module is intentionally small and
dependency-free so it can be unit-tested in isolation and reused elsewhere
(e.g. a future batch analysis script).

All temperatures are in FAHRENHEIT at the interface. Internal computation
converts to Celsius for the Magnus-Tetens formula, which is defined in °C.

See docs/methodology.md for the standards basis (NFRC 100/500, ISO 10077-2,
ISO 13788, ASHRAE 160).
"""

from __future__ import annotations
import math
from typing import Iterable


# Magnus-Tetens constants (ASHRAE Handbook of Fundamentals, Ch. 1)
_MAGNUS_A = 17.625
_MAGNUS_B = 243.04  # °C

# Working-hours filter config: Mon-Fri, 08:00-18:00
# Jan 1 of a TMY year is treated as Monday (standard TMY convention)
_WORK_START_HR = 8
_WORK_END_HR = 18   # exclusive
_WORKDAY_DOW = (0, 1, 2, 3, 4)  # Mon-Fri if Jan 1 is dow=0


# ------------------------------------------------------------
# Unit conversions
# ------------------------------------------------------------
def f_to_c(t_f: float) -> float:
    return (t_f - 32.0) * 5.0 / 9.0


def c_to_f(t_c: float) -> float:
    return t_c * 9.0 / 5.0 + 32.0


# ------------------------------------------------------------
# Magnus-Tetens dew point (accurate to ~0.4°C over HVAC range)
# ------------------------------------------------------------
def dew_point_c(t_c: float, rh_pct: float) -> float:
    """Compute dew point in °C given dry-bulb temp in °C and RH in %.

        γ = (a·T) / (b + T) + ln(RH/100)
        T_dew = (b·γ) / (a − γ)
    """
    if rh_pct <= 0:
        raise ValueError("RH must be positive")
    gamma = (_MAGNUS_A * t_c) / (_MAGNUS_B + t_c) + math.log(rh_pct / 100.0)
    return (_MAGNUS_B * gamma) / (_MAGNUS_A - gamma)


def dew_point_f(t_in_f: float, rh_pct: float) -> float:
    """Same as dew_point_c but accepts and returns °F."""
    return c_to_f(dew_point_c(f_to_c(t_in_f), rh_pct))


# ------------------------------------------------------------
# Occupancy filter
# ------------------------------------------------------------
def is_working_hour(h: int) -> bool:
    """True if hour index h (0..8759) falls in Mon-Fri 08:00-18:00."""
    day = h // 24
    hr = h % 24
    dow = day % 7  # Jan 1 = dow 0 (Mon) under TMY convention
    return dow in _WORKDAY_DOW and _WORK_START_HR <= hr < _WORK_END_HR


# ------------------------------------------------------------
# Main condensation analysis
# ------------------------------------------------------------
def analyze_condensation(
    t_out_hourly_f: list[float],
    f_factor: float,
    t_in_f: float,
    rh_in_pct: float,
) -> dict:
    """
    Run the full hourly condensation analysis.

    Parameters
    ----------
    t_out_hourly_f : list of 8760 outdoor dry-bulb temperatures (°F), one per hour
    f_factor       : cavity-side f-factor, 0-1 (from WINDOW/THERM)
    t_in_f         : indoor dry-bulb temp (°F)
    rh_in_pct      : indoor relative humidity (%)

    Returns
    -------
    dict with keys:
        t_dew_f           : indoor dew point (°F)
        t_surf_hourly_f   : 8760 predicted cavity-side surface temps (°F)
        condensation      : 8760 0/1 flags
        working           : 8760 0/1 flags for occupancy filter
        hours_total       : int — total 8760
        hours_all         : int — condensation hours across all hours
        hours_working     : int — condensation hours during working hours only
        hours_off         : int — condensation hours during off hours only
        pct_all           : float — hours_all / 8760 * 100
        pct_working       : float — hours_working / total_working_hours * 100
        pct_off           : float — hours_off / total_off_hours * 100
    """
    if len(t_out_hourly_f) != 8760:
        raise ValueError(f"Expected 8760 hourly values, got {len(t_out_hourly_f)}")
    if not 0.0 <= f_factor <= 1.0:
        raise ValueError(f"f_factor must be 0-1, got {f_factor}")

    # Indoor dew point — computed once, constant for the whole year
    t_dew_f = dew_point_f(t_in_f, rh_in_pct)

    # Hourly loop
    t_surf = [0.0] * 8760
    condensation = [0] * 8760
    working = [0] * 8760
    hours_all = 0
    hours_working = 0
    total_working = 0

    for h in range(8760):
        t_out = t_out_hourly_f[h]
        # T_surf = f · (T_in − T_out) + T_out
        ts = f_factor * (t_in_f - t_out) + t_out
        t_surf[h] = ts
        cond = 1 if ts < t_dew_f else 0
        condensation[h] = cond
        hours_all += cond

        if is_working_hour(h):
            working[h] = 1
            total_working += 1
            hours_working += cond

    total_off = 8760 - total_working
    hours_off = hours_all - hours_working

    return {
        "t_dew_f": round(t_dew_f, 2),
        "t_surf_hourly_f": [round(x, 2) for x in t_surf],
        "condensation": condensation,
        "working": working,
        "hours_total": 8760,
        "hours_all": hours_all,
        "hours_working": hours_working,
        "hours_off": hours_off,
        "pct_all": round(hours_all / 8760 * 100, 2),
        "pct_working": round(hours_working / total_working * 100, 2) if total_working else 0.0,
        "pct_off": round(hours_off / total_off * 100, 2) if total_off else 0.0,
        "total_working_hours": total_working,
        "total_off_hours": total_off,
    }
