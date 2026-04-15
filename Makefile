.PHONY: install setup clean

install:
	pip install -e .
	@echo "\n[✔] CRAB core installed."
	@echo "Launching setup wizard to configure benchmarks...\n"
	crab-setup

setup:
	crab-setup

clean:
	@echo "Cleaning up build artifacts..."
	rm -rf build/ dist/ *.egg-info
