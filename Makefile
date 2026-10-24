PYTHON ?= python

.PHONY: install data test

install:
	$(PYTHON) -m pip install -e ".[dev]"

data:
	$(PYTHON) -m desk.pipeline

test:
	$(PYTHON) -m pytest -q

.PHONY: monitor
monitor:
	$(PYTHON) -m desk.monitor
	$(PYTHON) -m desk.charts
