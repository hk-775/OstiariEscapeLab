PYTHON ?= python3.12

.PHONY: test validate demo clean

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

validate:
	PYTHONPATH=src $(PYTHON) -m escape_lab validate

demo:
	PYTHONPATH=src $(PYTHON) -m escape_lab demo

clean:
	PYTHONPATH=src $(PYTHON) -m escape_lab clean-artifacts --yes
