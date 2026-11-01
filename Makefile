PYTHON ?= python

.PHONY: data data-offline rebuild summary shells simulate validate-risk publish api dev test

data:  ## fetch due sources, rebuild the latest tables, print a summary
	$(PYTHON) -m src.main data

data-offline:  ## rebuild from the raw archive without downloading
	$(PYTHON) -m src.main data --offline

rebuild:  ## rebuild every processed table from the raw archive
	$(PYTHON) -m src.main rebuild

summary:
	$(PYTHON) -m src.main summary

shells:  ## altitude-band risk index for the latest snapshot
	$(PYTHON) -m src.main shells

simulate:  ## illustrative breakup simulator (800 km demo)
	$(PYTHON) -m src.main simulate

validate-risk:  ## shape, stability and sensitivity checks; writes docs/risk-validation.md
	$(PYTHON) scripts/validate_risk.py

publish:  ## score the latest screen and publish the results bundle
	$(PYTHON) -m src.main publish

api:  ## serve the read-only API on port 8000
	$(PYTHON) -m src.main api

dev:  ## API + web app together (fixture data if nothing is published)
	$(PYTHON) -m src.main dev

test:
	$(PYTHON) -m pytest
	cd web && npm test
