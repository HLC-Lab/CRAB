# Questo file contiene le costanti condivise per l'applicazione TUI
# per evitare errori di importazione circolare.

import os

# Walk: tui/ -> crab/ -> src/ -> CRAB_ROOT
CRAB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
PRESETS_FILE = os.path.join(CRAB_ROOT, "config", "presets.json")

SECTIONS = [
    "Experiments",
    "Global Options",
    "Environment",
    "Log",
]
