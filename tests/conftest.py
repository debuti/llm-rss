from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture():
    return lambda name: (FIX / name).read_bytes()
