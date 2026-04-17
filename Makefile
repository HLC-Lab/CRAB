.PHONY: install setup clean venv check_python

VENV_DIR = .venv
PIP = $(VENV_DIR)/bin/pip
CRAB_SETUP = $(VENV_DIR)/bin/crab-setup

# The Pre-Flight Check
check_python:
	@python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' || \
	(echo "\n[ERROR] CRAB requires Python 3.8 or higher." && \
	 echo "Current version: $$(python3 --version)" && \
	 echo "Suggestion: If you are on an HPC cluster, try running \`module load python\` before \`make install\`.\n" && exit 1)

# venv now depends on check_python succeeding
venv: check_python
	@echo "Creating virtual environment..."
	@python3 -m venv $(VENV_DIR)
	@$(PIP) install --upgrade pip > /dev/null 2>&1

install: venv
	@echo "============================================================"
	@echo "                 CRAB Installation Setup                    "
	@echo "============================================================"
	@read -p "? Do you want to install the interactive Textual UI? [y/N]: " install_tui; \
	if [ "$$install_tui" = "y" ] || [ "$$install_tui" = "Y" ]; then \
		echo "[+] Installing CRAB Core + TUI Support..."; \
		$(PIP) install -e ".[tui]"; \
	else \
		echo "[+] Installing CRAB Core only..."; \
		$(PIP) install -e .; \
	fi
	@echo "[✔] Installation complete."
	@echo "============================================================"
	@echo "Launching setup wizard to configure benchmarks...\n"
	@$(CRAB_SETUP)

setup:
	$(CRAB_SETUP)

clean:
	@echo "Cleaning up build artifacts..."
	rm -rf build/ dist/ *.egg-info $(VENV_DIR)
