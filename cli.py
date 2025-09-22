import os
import sys

# Aggiunge la directory 'src' al path di Python per trovare il pacchetto 'crab'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from crab.cli.orchestrator import run_from_cli

if __name__ == "__main__":
    run_from_cli()
