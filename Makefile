.PHONY: install setup clean venv _install_core _check_python

.DEFAULT_GOAL := install

VENV_DIR = .venv
PIP = $(VENV_DIR)/bin/pip
CRAB_SETUP = $(VENV_DIR)/bin/crab-setup

# 0. The Guardrail (Fails instantly if Python is too old)
_check_python:
	@python3 -c 'import sys; sys.exit(1) if sys.version_info < (3, 8) else sys.exit(0)' || \
	(echo "============================================================"; \
	 echo "[!] ERROR: CRAB requires Python 3.8 or higher."; \
	 echo "    You are currently using: $$(python3 --version 2>&1)"; \
	 echo ""; \
	 echo "    Suggestion: Run \`module load python\` or activate a modern"; \
	 echo "    Python environment before running \`make\` again."; \
	 echo "============================================================"; exit 1)

# 1. The Virtual Environment Builder
venv: _check_python
	python3 -m venv $(VENV_DIR)
	$(PIP) install --upgrade pip

# 2. The Traffic Cop (Checks if already installed)
install:
	@if [ -x "$(CRAB_SETUP)" ]; then \
		echo "============================================================"; \
		echo "[!] CRAB is already installed in this directory."; \
		echo ""; \
		echo "To configure new benchmarks, activate your environment:"; \
		echo "    source $(VENV_DIR)/bin/activate"; \
		echo "    crab-setup"; \
		echo ""; \
		echo "To completely reinstall, run \`make clean\` first."; \
		echo "============================================================"; \
	else \
		$(MAKE) _install_core; \
	fi

# 3. The Actual Installation
_install_core: venv
	$(PIP) install -e .
	@echo "\n[✔] CRAB core installed in virtual environment."
	@echo "Launching setup wizard to configure benchmarks...\n"
	$(CRAB_SETUP)

setup:
	$(CRAB_SETUP)

clean:
	@echo "Cleaning up build artifacts..."
	rm -rf build/ dist/ *.egg-info $(VENV_DIR)
