"""
test_nsrdb.py — standalone test of NSRDB TMY fetch.

Run this FIRST, before the FastAPI app, to verify your NREL API key works
and that the TMY data looks right.

Usage:
    cp .env.example .env
    # edit .env with your NREL_API_KEY and NREL_EMAIL
    python test_nsrdb.py
    python test_nsrdb.py 40.7580 -73.9855   # custom lat/lon (NYC default)
"""
import csv
import io
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

NSRDB_TMY_URL = "https://developer.nrel.gov/api/nsrdb/v2/solar/psm3-tmy-download.csv"

# Fields we actually need for the condensation calc
ATTRIBUTES = "air_temperature,dew_point,relative_humidity"


def fetch_tmy(lat: float, lon: float, api_key: str, email: str) -> str:
    """Fetch TMY CSV from NSRDB. Returns the raw CSV text."""
    params = {
        "api_key": api_key,
        "email": email,
        "full_name": "INOVUES",
        "affiliation": "INOVUES",
        "reason": "Secondary window retrofit condensation analysis",
        "mailing_list": "false",
        "wkt": f"POINT({lon} {lat})",   # NOTE: lon first, then lat
        "names": "tmy",
        "attributes": ATTRIBUTES,
        "leap_day": "false",
        "utc": "false",
        "interval": "60",
    }
    print(f"→ Requesting TMY for lat={lat}, lon={lon}...")
    with httpx.Client(timeout=60) as client:
        r = client.get(NSRDB_TMY_URL, params=params)
    if r.status_code != 200:
        print(f"✗ HTTP {r.status_code}")
        print(r.text[:500])
        sys.exit(1)
    print(f"✓ OK — received {len(r.text):,} bytes")
    return r.text


def parse_tmy(csv_text: str):
    """
    NSRDB TMY CSV layout:
      Row 0: metadata header (Source, Location ID, City, State, ...)
      Row 1: metadata values
      Row 2: data column header (Year, Month, Day, Hour, Minute, Temperature, ...)
      Row 3+: 8,760 hourly rows
    Returns (metadata_dict, list_of_hour_dicts).
    """
    reader = csv.reader(io.StringIO(csv_text))
    meta_header = next(reader)
    meta_values = next(reader)
    metadata = dict(zip(meta_header, meta_values))

    data_header = next(reader)
    hours = []
    for row in reader:
        hours.append(dict(zip(data_header, row)))

    return metadata, hours


def main():
    api_key = os.getenv("NREL_API_KEY")
    email = os.getenv("NREL_EMAIL")

    if not api_key or api_key == "paste_your_key_here":
        print("✗ No NREL_API_KEY in .env")
        print("  Register at https://developer.nrel.gov/signup/ then add it to .env")
        sys.exit(1)
    if not email:
        print("✗ No NREL_EMAIL in .env")
        sys.exit(1)

    # Default: NYC (Empire State Building area)
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else 40.7580
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else -73.9855

    csv_text = fetch_tmy(lat, lon, api_key, email)

    # Save raw CSV for inspection
    out_dir = Path("test_output")
    out_dir.mkdir(exist_ok=True)
    raw_path = out_dir / f"tmy_{lat}_{lon}.csv"
    raw_path.write_text(csv_text)
    print(f"✓ Saved raw CSV: {raw_path}")

    metadata, hours = parse_tmy(csv_text)

    # --- Sanity checks ---
    print()
    print("=" * 60)
    print("METADATA")
    print("=" * 60)
    for k in ["Source", "Location ID", "City", "State", "Country",
              "Latitude", "Longitude", "Time Zone", "Elevation"]:
        if k in metadata:
            print(f"  {k:14s}: {metadata[k]}")

    print()
    print("=" * 60)
    print(f"HOURLY DATA — {len(hours):,} rows (expected 8,760)")
    print("=" * 60)

    if len(hours) != 8760:
        print(f"⚠  Unexpected row count: {len(hours)}")

    # Check the three fields we need
    required = ["Temperature", "Dew Point", "Relative Humidity"]
    missing = [f for f in required if f not in hours[0]]
    if missing:
        print(f"✗ Missing required columns: {missing}")
        print(f"  Available: {list(hours[0].keys())}")
        sys.exit(1)
    print(f"✓ All required fields present: {required}")

    # Summary stats
    temps = [float(h["Temperature"]) for h in hours]
    dews = [float(h["Dew Point"]) for h in hours]
    rhs = [float(h["Relative Humidity"]) for h in hours]

    print()
    print(f"  Temperature   (°C): min={min(temps):6.1f}  "
          f"mean={sum(temps)/len(temps):6.1f}  max={max(temps):6.1f}")
    print(f"  Dew Point     (°C): min={min(dews):6.1f}  "
          f"mean={sum(dews)/len(dews):6.1f}  max={max(dews):6.1f}")
    print(f"  Relative Hum. (%):  min={min(rhs):6.1f}  "
          f"mean={sum(rhs)/len(rhs):6.1f}  max={max(rhs):6.1f}")

    # Show first 3 and last 3 rows as a spot check
    print()
    print("FIRST 3 HOURS:")
    for h in hours[:3]:
        print(f"  {h['Year']}-{h['Month'].zfill(2)}-{h['Day'].zfill(2)} "
              f"{h['Hour'].zfill(2)}:00  "
              f"T={h['Temperature']}°C  "
              f"Td={h['Dew Point']}°C  "
              f"RH={h['Relative Humidity']}%")

    print()
    print("✓ All checks passed. NSRDB fetch is working.")


if __name__ == "__main__":
    main()
