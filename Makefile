.PHONY: setup test validate-sample validate-full clean

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	@echo "Now: source .venv/bin/activate, cp .env.example .env and fill it in"

test:
	python3 -m pytest tests/ -v

validate-sample:
	python3 scripts/09_validate_corpus.py --limit 100

validate-full:
	python3 scripts/09_validate_corpus.py --skip-enumerate --limit 0
	@echo "Point --in at your own full-corpus CSV via the individual scripts for a non-sample run."

clean:
	rm -rf .venv __pycache__ scripts/__pycache__ scripts/utils/__pycache__ .pytest_cache
	rm -f data/sample_output/*.csv data/sample_output/*.json
