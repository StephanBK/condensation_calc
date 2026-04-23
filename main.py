"""
main.py — FastAPI backend for INOVUES cavity-side condensation calculator.

Endpoints:
    GET /health              -> {"status": "ok"}
    GET /geocode             -> address -> lat/lon via US Census Geocoder
    GET /weather             -> lat/lon -> 8760 outdoor temps (°F) via NSRDB TMY
    GET /calculate           -> full pipeline: address + f + indoor -> analysis
    GET /                    -> serves the frontend (added in Chunk 3)
"""

from __future__ import annotations
import csv
import io
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from physics import analyze_condensation, c_to_f

load_dotenv()

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------
NREL_API_KEY = os.getenv("NREL_API_KEY", "")
NREL_EMAIL = os.getenv("NREL_EMAIL", "")
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN", "")

CENSUS_GEOCODER_URL = (
    "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
)
NSRDB_TMY_URL = (
    "https://developer.nrel.gov/api/nsrdb/v2/solar/nsrdb-GOES-tmy-v4-0-0-download.csv"
)

# In-memory weather cache: key=(round(lat,2), round(lon,2)) -> dict
# NSRDB snaps requests to a ~4 km grid anyway, so rounding lat/lon to 0.01°
# (~1 km) gives us very effective cache hits across nearby addresses.
_WEATHER_CACHE: dict[tuple[float, float], dict] = {}


app = FastAPI(title="INOVUES Condensation Calculator", version="0.1.0")


# ------------------------------------------------------------
# Utilities
# ------------------------------------------------------------
def _cache_key(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 2), round(lon, 2))


async def geocode_address(address: str) -> dict:
    """US Census Geocoder: address -> lat/lon + matched address.
    Free, no API key required."""
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(CENSUS_GEOCODER_URL, params=params)
    if r.status_code != 200:
        raise HTTPException(502, f"Census geocoder HTTP {r.status_code}")

    data = r.json()
    matches = data.get("result", {}).get("addressMatches", [])
    if not matches:
        raise HTTPException(404, f"Address not found: {address}")

    m = matches[0]
    coords = m["coordinates"]  # {x: lon, y: lat}
    return {
        "matched_address": m["matchedAddress"],
        "lat": float(coords["y"]),
        "lon": float(coords["x"]),
    }


async def fetch_tmy(lat: float, lon: float) -> dict:
    """Fetch TMY outdoor dry-bulb temperatures from NSRDB.
    Returns {metadata, t_out_hourly_f}. Cached in-memory by rounded lat/lon."""
    key = _cache_key(lat, lon)
    if key in _WEATHER_CACHE:
        cached = _WEATHER_CACHE[key]
        return {**cached, "cached": True}

    if not NREL_API_KEY or not NREL_EMAIL:
        raise HTTPException(500, "NREL_API_KEY or NREL_EMAIL not set in environment")

    params = {
        "api_key": NREL_API_KEY,
        "email": NREL_EMAIL,
        "full_name": "INOVUES",
        "affiliation": "INOVUES",
        "reason": "Secondary window retrofit condensation analysis",
        "mailing_list": "false",
        "wkt": f"POINT({lon} {lat})",
        "names": "tmy",
        "attributes": "air_temperature",
        "leap_day": "false",
        "utc": "false",
        "interval": "60",
    }
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(NSRDB_TMY_URL, params=params)
    if r.status_code != 200:
        raise HTTPException(502, f"NSRDB HTTP {r.status_code}: {r.text[:200]}")

    # Parse: 2 metadata rows + 1 data header + 8760 data rows
    reader = csv.reader(io.StringIO(r.text))
    meta_header = next(reader)
    meta_values = next(reader)
    metadata = dict(zip(meta_header, meta_values))
    data_header = next(reader)

    if "Temperature" not in data_header:
        raise HTTPException(
            502, f"NSRDB response missing Temperature column. Got: {data_header}"
        )
    t_idx = data_header.index("Temperature")

    # NSRDB Temperature is °C; convert to °F on the way in
    t_out_hourly_f = []
    for row in reader:
        t_c = float(row[t_idx])
        t_out_hourly_f.append(round(c_to_f(t_c), 2))

    if len(t_out_hourly_f) != 8760:
        raise HTTPException(
            502, f"NSRDB returned {len(t_out_hourly_f)} rows, expected 8760"
        )

    result = {
        "metadata": {
            "source": metadata.get("Source", ""),
            "location_id": metadata.get("Location ID", ""),
            "city": metadata.get("City", ""),
            "state": metadata.get("State", ""),
            "grid_lat": float(metadata.get("Latitude", 0)),
            "grid_lon": float(metadata.get("Longitude", 0)),
            "time_zone": metadata.get("Time Zone", ""),
            "elevation_m": metadata.get("Elevation", ""),
        },
        "t_out_hourly_f": t_out_hourly_f,
        "cached": False,
    }
    _WEATHER_CACHE[key] = {k: v for k, v in result.items() if k != "cached"}
    return result


# ------------------------------------------------------------
# Routes
# ------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "nrel_key_configured": bool(NREL_API_KEY),
        "mapbox_token_configured": bool(MAPBOX_TOKEN),
        "cache_size": len(_WEATHER_CACHE),
    }


@app.get("/config")
def config():
    """Expose frontend-safe config values (like the Mapbox public token)."""
    return {"mapbox_token": MAPBOX_TOKEN}


@app.get("/geocode")
async def geocode(address: str = Query(..., min_length=3)):
    """Resolve a US address to lat/lon."""
    return await geocode_address(address)


@app.get("/weather")
async def weather(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    """Fetch 8760 hourly outdoor dry-bulb temperatures (°F) for a point."""
    return await fetch_tmy(lat, lon)


@app.get("/calculate")
async def calculate(
    address: str = Query(..., min_length=3, description="US address"),
    f: float = Query(..., ge=0.0, le=1.0, description="Cavity-side f-factor (0-1)"),
    t_in: float = Query(70.0, description="Indoor dry-bulb temp (°F)"),
    rh_in: float = Query(35.0, ge=1, le=100, description="Indoor RH (%)"),
    include_hourly: bool = Query(
        True, description="Include 8760-hour arrays in response (adds ~100KB)"
    ),
):
    """Full pipeline: address -> lat/lon -> TMY weather -> condensation analysis."""
    geo = await geocode_address(address)
    wx = await fetch_tmy(geo["lat"], geo["lon"])
    result = analyze_condensation(
        t_out_hourly_f=wx["t_out_hourly_f"],
        f_factor=f,
        t_in_f=t_in,
        rh_in_pct=rh_in,
    )

    response: dict = {
        "inputs": {
            "address": address,
            "f_factor": f,
            "t_in_f": t_in,
            "rh_in_pct": rh_in,
        },
        "location": {
            "matched_address": geo["matched_address"],
            "lat": geo["lat"],
            "lon": geo["lon"],
            "nsrdb_grid": {
                "lat": wx["metadata"]["grid_lat"],
                "lon": wx["metadata"]["grid_lon"],
                "elevation_m": wx["metadata"]["elevation_m"],
                "time_zone": wx["metadata"]["time_zone"],
            },
            "weather_cached": wx["cached"],
        },
        "summary": {
            "t_dew_f": result["t_dew_f"],
            "hours_total": result["hours_total"],
            "hours_all": result["hours_all"],
            "hours_working": result["hours_working"],
            "hours_off": result["hours_off"],
            "pct_all": result["pct_all"],
            "pct_working": result["pct_working"],
            "pct_off": result["pct_off"],
            "total_working_hours": result["total_working_hours"],
            "total_off_hours": result["total_off_hours"],
        },
    }
    if include_hourly:
        response["hourly"] = {
            "t_out_f": wx["t_out_hourly_f"],
            "t_surf_f": result["t_surf_hourly_f"],
            "condensation": result["condensation"],
            "working": result["working"],
        }
    return response


@app.get("/")
def root():
    """Serve the frontend HTML."""
    return FileResponse("static/index.html")


# Static file serving (for future CSS/JS/images if we split them out)
app.mount("/static", StaticFiles(directory="static"), name="static")
