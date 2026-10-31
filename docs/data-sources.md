# Data sources

All sources are free and need no login. Terms were read on 2026-10-30 from
CelesTrak's [usage policy](https://celestrak.org/usage-policy.php) (updated 2026-05-22)
and [GP data formats](https://celestrak.org/NORAD/documentation/gp-data-formats.php)
page (updated 2026-06-23). Re-read them before changing the downloader.

| Source | URL | Updates | Perigee polls at most |
|---|---|---|---|
| GP orbit data (per group) | `https://celestrak.org/NORAD/elements/gp.php?GROUP=<group>&FORMAT=csv` | every 2 h | every 2 h |
| SATCAT | `https://celestrak.org/pub/satcat.csv` | once or twice a day | every 12 h |
| Space weather (last 5 years) | `https://celestrak.org/SpaceData/SW-Last5Years.csv` | every 3 h | every 24 h |

| SOCRATES results (validation only) | `https://celestrak.org/SOCRATES/sort-minRange.csv` (~22 MB) | about every 10-11 h | by hand, once per validation |

SOCRATES is downloaded by hand into `data/reference/socrates/` to validate screening
(`docs/screening-validation.md`). Its directory listing `SOCRATES/jsonDir.php` may be
queried at most once an hour. Never use SOCRATES as ML labels.

Default GP groups: `active`, `analyst`, `cosmos-2251-debris`, `iridium-33-debris`,
`fengyun-1c-debris` (set `PERIGEE_GP_GROUPS` to change). CelesTrak has no single
"full catalog" GP query; the complete ~30,000-object public catalog, including all
debris, needs Space-Track (not yet integrated).

## Rules the downloader follows

- **Download once per update.** Each source has a minimum interval, recorded in
  `data/state/fetch_log.json` *before* the request, so a failed or rejected
  download still counts against the interval.
- **Stop on any non-200 response.** The policy says machine clients must stop
  immediately on any HTTP 301/403/404/50x and report it to a human, or the IP is
  firewalled. Perigee does not follow redirects, never retries an HTTP error,
  and writes `data/state/STOP_CELESTRAK`. No further requests are made until a
  human investigates and deletes that file.
- **Retry only network failures** (timeouts, dropped connections), with
  exponential backoff (5 s, 10 s, ...), up to `PERIGEE_HTTP_MAX_ATTEMPTS`.
- **Descriptive User-Agent** naming the project and repo.
- **CSV format**: CelesTrak's recommendation. It handles 6-digit catalog numbers
  (100000+), which the TLE format cannot, and is a third the size of JSON.
- **Only the last 5 years of space weather**: the policy asks users not to
  download 60 years of history every time.

## Storage

```
data/raw/<source>/<snapshot_id>/<file>.csv.gz         accepted downloads, never modified
data/quarantine/<source>/<snapshot_id>/<file>.csv.gz  rejected downloads, kept for audit
data/processed/catalog_snapshots/snapshot_date=YYYY-MM-DD/<snapshot_id>.parquet
data/processed/satcat/satcat.parquet
data/processed/space_weather/space_weather.parquet
data/state/fetch_log.json, data/state/STOP_CELESTRAK
```

Every processed table can be rebuilt from `data/raw` with `python -m src.main rebuild`.

## Data quality checks

A download is **rejected** (quarantined; the last good data stays live) when:
- it is not the expected CSV (missing columns, HTML error page, empty);
- a GP group returns no rows;
- a catalog number appears twice within one GP group or in SATCAT, or a date repeats in space weather;
- the row count is below the floor (`PERIGEE_GP_MIN_OBJECTS`, `PERIGEE_SATCAT_MIN_OBJECTS`,
  `PERIGEE_SW_MIN_DAYS`) or fell more than `PERIGEE_MAX_COUNT_DROP` (20%) since the last snapshot;
- more than `PERIGEE_MAX_BAD_ROW_FRACTION` (1%) of rows fail the row checks below.

Individual rows are **excluded and counted** (in the JSON log) when the epoch is
missing or in the future, eccentricity is outside [0, 1), mean motion is not
positive, or inclination is outside [0, 180]. An object in several GP groups
keeps its freshest record.

## Space-Track (later)

Space-Track needs a free account and its user agreement restricts redistributing
raw data: publish code and derived results, not their raw archive. Credentials go
in environment variables or GitHub Secrets only, never in the repo.
