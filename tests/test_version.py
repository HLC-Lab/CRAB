"""The package version has one source: crab.__version__, read by pyproject and the contract."""

from importlib.metadata import version

import crab
from crab.cli import contract


def test_installed_metadata_matches_the_source_version() -> None:
    assert version("crab") == crab.__version__


def test_crab_info_reports_the_source_version() -> None:
    assert contract.gather_info()["crab_version"] == crab.__version__
