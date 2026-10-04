import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))  # the scripts are plain modules, importable without installing

FIXTURES = REPO / "tests" / "fixtures"


@pytest.fixture
def project(tmp_path):
    """A writable copy of the synthetic Android project in tests/fixtures."""
    root = tmp_path / "project"
    shutil.copytree(FIXTURES, root)
    return root
