.PHONY: install setup clean venv _install_core

VENV_DIR = .venv
PIP = $(VENV_DIR)/bin/pip
CRAB_SETUP = $(VENV_DIR)/bin/crab-setup


# Checks if is already installed
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

venv:
	python3 -m venv $(VENV_DIR)
	$(PIP) install --upgrade pip

# 2. The Actual Installation (Only runs if the check above passes)
_install_core: venv
	$(PIP) install -e .
	@echo "\n[✔] CRAB core installed in virtual environment."
	@echo "Launching setup wizard to configure benchmarks..."
	$(CRAB_SETUP)

setup:
	$(CRAB_SETUP)

clean:
	@echo "Cleaning up build artifacts..."
	rm -rf build/ dist/ *.egg-info $(VENV_DIR)
