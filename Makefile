PYTHON ?= python3.12

.PHONY: test lint validate demo sync-data check-data build clean

test: check-data
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

lint:
	$(PYTHON) -m ruff check src tests scripts

validate:
	PYTHONPATH=src $(PYTHON) -m escape_lab validate

demo:
	PYTHONPATH=src $(PYTHON) -m escape_lab demo

sync-data:
	$(PYTHON) scripts/sync_package_data.py

check-data:
	$(PYTHON) scripts/sync_package_data.py --check

build: check-data
	$(PYTHON) -m build

clean:
	PYTHONPATH=src $(PYTHON) -m escape_lab clean-artifacts --yes
