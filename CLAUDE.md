# Project: Perigee (Orbital Debris Risk Tracker)

## 1. Project Summary
Perigee is a web app that ingests public orbit data for ~30,000 space objects to calculate and visualize orbital risks. It performs four main functions:
1. 3D real-time visualization of objects.
2. Conjunction screening (finding upcoming close approaches).
3. ML risk assessment to classify if flagged approaches are likely dangerous or just false alarms caused by public TLE inaccuracy.
4. Shell risk scoring for 25 km altitude bands and collision simulation.

## 2. Tech Stack
- **Backend:** Python 3.11+, FastAPI
- **Data:** DuckDB/SQLite, Parquet, pandas, numpy
- **Math/Algorithms:** sgp4 (Python), scipy.spatial.cKDTree
- **ML:** scikit-learn, XGBoost/LightGBM
- **Frontend:** HTML/JS, satellite.js, CesiumJS (3D globe)
- **Infra:** GitHub Actions (CRON data jobs)

## 3. Engineering Constraints & Rules
- **Resource Limits:** The app will run on free tiers. Optimize memory heavily. Do not stream 30k positions from the server; let client-side `satellite.js` handle high-frequency positional math.
- **Data Etiquette:** Do not aggressively poll CelesTrak. Cache data locally in Parquet/DuckDB.
- **ML Integrity:** Time-based splits for train/test are mandatory to prevent data leakage. Use PR-AUC for heavily imbalanced conjunction data.
- **Honest Framing:** Never use "collision prediction." Always use "risk index" or "estimated close approach based on public data."

## 4. Execution Rules for Claude Code
- Follow the user's specific Phase prompts exactly.
- Complete and commit the current phase's "Done when" criteria before moving on.
- Write tests alongside each module (e.g., `test_data.py`) to prevent costly late-stage debugging.
- Keep data fetching, orbital physics, ML, and API routing in separate modules.

## 5. Local Setup
- Virtual env: `.venv` (Python 3.12). Activate with `.venv\Scripts\activate` (Windows).
- Install: `pip install -r requirements.txt`
- Test: `python -m pytest`
- Dependencies are pinned in `requirements.txt`. Note: DuckDB 2.0 relations have no `fetchone()`; use `fetchall()` or `.limit(1).fetchall()`.
