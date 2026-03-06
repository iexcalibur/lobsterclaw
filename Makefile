SHELL := /bin/bash

PYTHON ?= python3
VENV ?= .venv
VENV_BIN := $(VENV)/bin
PIP := $(VENV_BIN)/pip
PY := $(VENV_BIN)/python
NPM := npm
GATEWAY_UI := gateway_ui
GATEWAY_KEY := $(shell grep '^GATEWAY_API_KEY=' .env 2>/dev/null | cut -d= -f2- || true)

.PHONY: help venv install install-ui setup check-env run run-debug test clean \
        gateway-ui gateway-ui-build all stop

help:
	@echo ""
	@echo "  LobsterClaw — Mission Control"
	@echo "  ========================"
	@echo ""
	@echo "  Setup:"
	@echo "    make setup          Create venv, install Python deps, validate config"
	@echo "    make install-ui     Install Node.js deps for the Gateway UI"
	@echo ""
	@echo "  Run:"
	@echo "    make run            Start the Telegram bot + Gateway API (port 4400)"
	@echo "    make run-debug      Same as 'run' with LOG_LEVEL=DEBUG"
	@echo "    make gateway-ui     Start the Gateway UI dev server (port 3001)"
	@echo "    make all            Start bot + Gateway API + Gateway UI together"
	@echo ""
	@echo "  Build:"
	@echo "    make gateway-ui-build  Build the Gateway UI for production"
	@echo ""
	@echo "  Other:"
	@echo "    make test           Run the test suite"
	@echo "    make clean          Remove caches and build artifacts"
	@echo ""

# ---------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------

venv:
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)

install: venv
	@$(PIP) install --upgrade pip
	@$(PIP) install -r requirements.txt

setup: install check-env

check-env: venv
	@$(PY) -c "from config import get_config; c=get_config(); print(f'Config OK: provider={c.llm_provider}, model={c.llm_model}, dm_policy={c.telegram_dm_policy}')"

# ---------------------------------------------------------------
# Gateway UI (Next.js)
# ---------------------------------------------------------------

install-ui:
	@cd $(GATEWAY_UI) && $(NPM) install

gateway-ui: install-ui
	@echo "[Gateway UI] Starting dev server on http://localhost:3001"
	@cd $(GATEWAY_UI) && NEXT_PUBLIC_GATEWAY_API_KEY="$(GATEWAY_KEY)" $(NPM) run dev

gateway-ui-build: install-ui
	@echo "[Gateway UI] Building for production..."
	@cd $(GATEWAY_UI) && NEXT_PUBLIC_GATEWAY_API_KEY="$(GATEWAY_KEY)" $(NPM) run build

# ---------------------------------------------------------------
# Run services
# ---------------------------------------------------------------

run: check-env
	@echo "[LobsterClaw] Starting bot + Gateway API..."
	@GATEWAY_ENABLED=true $(PY) main.py

run-debug: check-env
	@LOG_LEVEL=DEBUG GATEWAY_ENABLED=true $(PY) main.py

# Start everything: bot + gateway API + gateway UI (in parallel)
all: check-env install-ui
	@echo "[LobsterClaw] Starting all services..."
	@echo "  → Bot + Gateway API (Python)"
	@echo "  → Gateway UI (Next.js on :3001)"
	@echo ""
	@trap 'kill 0' EXIT; \
	GATEWAY_ENABLED=true $(PY) main.py & \
	(cd $(GATEWAY_UI) && NEXT_PUBLIC_GATEWAY_API_KEY="$(GATEWAY_KEY)" $(NPM) run dev) & \
	wait

# ---------------------------------------------------------------
# Test
# ---------------------------------------------------------------

test: venv
	@$(VENV_BIN)/pytest -q

# ---------------------------------------------------------------
# Clean
# ---------------------------------------------------------------

clean:
	@find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	@rm -rf .pytest_cache
	@rm -rf $(GATEWAY_UI)/.next $(GATEWAY_UI)/node_modules
