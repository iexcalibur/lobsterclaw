SHELL := /bin/bash

PYTHON ?= python3
VENV ?= .venv
VENV_BIN := $(VENV)/bin
PIP := $(VENV_BIN)/pip
PY := $(VENV_BIN)/python

.PHONY: help venv install setup check-env run run-debug test clean

help:
	@echo "Targets:"
	@echo "  make setup      - Create venv, install deps, and validate config"
	@echo "  make run        - Run the Telegram bot"
	@echo "  make run-debug  - Run the bot with LOG_LEVEL=DEBUG"
	@echo "  make test       - Run test suite"
	@echo "  make clean      - Remove local caches"

venv:
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)

install: venv
	@$(PIP) install --upgrade pip
	@$(PIP) install -r requirements.txt

setup: install check-env

check-env: venv
	@$(PY) -c "from config import get_config; c=get_config(); print(f'Config OK: provider={c.llm_provider}, model={c.llm_model}, dm_policy={c.telegram_dm_policy}')"

run: check-env
	@$(PY) main.py

run-debug: check-env
	@LOG_LEVEL=DEBUG $(PY) main.py

test: venv
	@$(VENV_BIN)/pytest -q

clean:
	@find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	@rm -rf .pytest_cache
