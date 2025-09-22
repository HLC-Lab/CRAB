import os
import sys

# Aggiunge la directory 'src' al path di Python per trovare il pacchetto 'crab'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from crab.tui.app import BenchmarkApp

if __name__ == "__main__":
    app = BenchmarkApp()
    app.run()
