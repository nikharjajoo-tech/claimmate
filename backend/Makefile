.PHONY: install test eval eval-smoke serve

PY := .venv/bin/python

install:
	python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"

test:
	$(PY) -m pytest -q

eval-smoke:
	$(PY) -m app.eval --limit 5

eval:
	$(PY) -m app.eval $(ARGS)

serve:
	$(PY) -m uvicorn app.api.main:app --reload --port 8000
