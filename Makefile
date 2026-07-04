.PHONY: install setup clean venv _install_core _check_python \
        verify verify-full fmt _verify_py _verify_fe _static_sync

.DEFAULT_GOAL := install

VENV_DIR = .venv
PIP = $(VENV_DIR)/bin/pip
CRAB_BIN = $(VENV_DIR)/bin/crab
PY = $(VENV_DIR)/bin/python
WEBUI = src/crab/webui

# --- Developer verification gates ------------------------------------------
# `make verify` after every change; `make verify-full` before finishing a task
# or whenever frontend source changed. See docs/dev/dashboard/testing.md.

verify: _verify_py _verify_fe
	@echo "[verify] all green"

_verify_py:
	$(VENV_DIR)/bin/ruff check src tests
	$(VENV_DIR)/bin/ruff format --check src tests
	$(VENV_DIR)/bin/mypy
	$(PY) -m pytest -q

_verify_fe:
	cd $(WEBUI) && npx prettier --check src tests
	cd $(WEBUI) && npx eslint src tests/unit
	cd $(WEBUI) && npm run --silent type-check
	cd $(WEBUI) && npx vitest run --silent

verify-full: verify
	cd $(WEBUI) && npm run --silent build
	$(MAKE) _static_sync
	cd $(WEBUI) && npm run --silent gen:api
	@git diff --quiet -- $(WEBUI)/src/api/generated.ts || \
	(echo "[!] src/api/generated.ts is stale against the backend's OpenAPI schema."; \
	 echo "    Commit the regenerated file (npm run gen:api) with the backend change."; exit 1)
	@if [ -f $(WEBUI)/playwright.config.ts ]; then \
	    cd $(WEBUI) && npx playwright test; \
	else \
	    echo "[verify-full] playwright not set up yet, skipping e2e"; \
	fi
	@echo "[verify-full] all green"

# The built SPA in src/crab/web/static is committed and shipped in the wheel;
# it must always match the frontend source (see ADR-004).
_static_sync:
	@git diff --quiet -- src/crab/web/static || \
	(echo "[!] src/crab/web/static is out of sync with the committed build."; \
	 echo "    Commit the rebuilt assets together with the source change."; exit 1)

fmt:
	$(VENV_DIR)/bin/ruff check --fix src tests
	$(VENV_DIR)/bin/ruff format src tests
	cd $(WEBUI) && npx prettier --write src tests

# 0. The Guardrail (Fails instantly if Python is too old)
_check_python:
	@python3 -c 'import sys; sys.exit(1) if sys.version_info < (3, 10) else sys.exit(0)' || \
	(echo "============================================================"; \
	 echo "[!] ERROR: CRAB requires Python 3.10 or higher."; \
	 echo "    You are currently using: $$(python3 --version 2>&1)"; \
	 echo ""; \
	 echo "    Suggestion: Run \`module load python\` or activate a modern"; \
	 echo "    Python environment before running \`make\` again."; \
	 echo "============================================================"; exit 1)

# 1. The Virtual Environment Builder
venv: _check_python
	python3 -m venv $(VENV_DIR)
	$(PIP) install --upgrade pip
	$(PIP) install argcomplete
	@echo 'eval "$$(register-python-argcomplete crab)"' >> $(VENV_DIR)/bin/activate
	@echo 'register-python-argcomplete --shell fish crab | source' >> $(VENV_DIR)/bin/activate.fish

# 2. The Traffic Cop (Checks if already installed)
install:
	@if [ -x "$(CRAB_BIN)" ]; then \
	    echo "============================================================"; \
	    echo "[!] CRAB is already installed in this directory."; \
	    echo ""; \
	    echo "To configure new benchmarks, activate your environment:"; \
	    echo "    source $(VENV_DIR)/bin/activate"; \
	    echo "    crab setup"; \
	    echo ""; \
	    echo "To completely reinstall, run \`make clean\` first."; \
	    echo "============================================================"; \
	else \
	    $(MAKE) _install_core; \
	fi

# 3. The Actual Installation
_install_core: venv
	$(PIP) install -e .
	@echo "[✔] CRAB core installed in virtual environment."
	@echo "Launching setup wizard to configure benchmarks..."
	$(CRAB_BIN) setup

setup:
	$(CRAB_BIN) setup

clean:
	@echo "Cleaning up build artifacts..."
	rm -rf build/ dist/ *.egg-info $(VENV_DIR)
