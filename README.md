# INOVUES Cavity-Side Condensation Calculator

Predicts hourly cavity-side condensation risk for Secondary Window Retrofits (SWR) based on:
- Location (address → lat/lon via US Census Geocoder)
- Hourly weather (NSRDB TMY via NREL API)
- User-supplied f-factor (from LBNL WINDOW/THERM)
- Indoor temperature + relative humidity

## Method

For each hour `h` of the TMY year:

```
T_surf(h) = f · (T_in − T_out(h)) + T_out(h)
condensation(h) = 1   if   T_surf(h) < T_dew(T_in, RH_in)
```

See `docs/methodology.md` for the full derivation and standards basis
(NFRC 100 / 500, ISO 10077-2, ISO 13788, ASHRAE 160).

## Stack

| Layer         | Tech                                    |
|---------------|-----------------------------------------|
| Backend       | FastAPI on Railway                      |
| Geocoding     | US Census Geocoder (free, no key)       |
| Weather       | NREL NSRDB TMY API (requires free key)  |
| Frontend      | Vanilla HTML + React (from CDN)         |
| Cache         | In-memory Python dict (per process)     |

## Setup (local)

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env — paste your NREL API key
python test_nsrdb.py       # verify NSRDB pull works
uvicorn main:app --reload  # run the API
```

## Env vars

| Var              | Required | Notes                                      |
|------------------|----------|--------------------------------------------|
| `NREL_API_KEY`   | yes      | developer.nrel.gov/signup                  |
| `NREL_EMAIL`     | yes      | NSRDB requires a contact email             |
| `PORT`           | no       | Railway sets this automatically            |

## Endpoints

| Route        | Method | Notes                                      |
|--------------|--------|--------------------------------------------|
| `/`          | GET    | Serves the calculator UI                   |
| `/health`    | GET    | `{"status": "ok"}`                         |
| `/geocode`   | GET    | `?address=...` → `{lat, lon, matched}`     |
| `/weather`   | GET    | `?lat=...&lon=...` → 8,760 hourly records  |
| `/calculate` | GET    | Full pipeline: address + f + T_in + RH_in  |

## NSRDB notes

- TMY product: `psm3-tmy-download.csv` (continental US, pre-computed typical year)
- Fields we fetch: `air_temperature` (outdoor dry-bulb only)
  - Indoor temperature and RH are **user inputs** in the calculator UI
  - Indoor dew point is computed via Magnus–Tetens from those user inputs
- Grid resolution: ~4 km
- Rate limit (free tier): 1,000 req/hour, 10,000/day
- We cache per-call in memory to avoid redundant pulls within a session
