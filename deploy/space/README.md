---
title: Perigee API
emoji: 🛰️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: Read-only estimates of close approaches from public orbit data
---

# Perigee API

Read-only API behind [Perigee](https://github.com/aryanpol73/perigee): estimated close
approaches, risk indices and orbital crowding from public orbit data. **Estimates from
public data; not for operational use.** Data from CelesTrak and Space-Track.org
(USSPACECOM).

This Space is deployed automatically from the GitHub repository (`.github/workflows/deploy.yml`);
do not edit it here. Endpoints are under `/v1` (`/v1/health`, `/v1/meta`, `/v1/conjunctions`, ...);
the schema is at `/docs`.
