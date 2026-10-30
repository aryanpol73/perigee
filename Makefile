PYTHON ?= python

.PHONY: data data-offline rebuild summary test

data:  ## fetch due sources, rebuild the latest tables, print a summary
	$(PYTHON) -m src.main data

data-offline:  ## rebuild from the raw archive without downloading
	$(PYTHON) -m src.main data --offline

rebuild:  ## rebuild every processed table from the raw archive
	$(PYTHON) -m src.main rebuild

summary:
	$(PYTHON) -m src.main summary

test:
	$(PYTHON) -m pytest
