.PHONY: install setup clean venv

VENV_DIR = .venv
PIP = $(VENV_DIR)/bin/pip
CRAB_SETUP = $(VENV_DIR)/bin/crab-setup

venv:
	python3 -m venv $(VENV_DIR)
	$(PIP) install --upgrade pip

install: venv
	$(PIP) install -e .
	@echo "\n[✔] CRAB core installed in virtual environment."
	@echo "Launching setup wizard to configure benchmarks...\n"
	$(CRAB_SETUP)

setup:
	$(CRAB_SETUP)

clean:
	@echo "Cleaning up build artifacts..."
	rm -rf build/ dist/ *.egg-info $(VENV_DIR)
