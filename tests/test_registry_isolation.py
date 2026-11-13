"""Every test runs with its own apps list, so none can leak entries into the real one."""

import os
from pathlib import Path

from coderstanding.apps import DATA_HOME_ENV, REGISTRY_ENV, registry_path


def test_every_test_gets_a_temp_registry_and_data_home(tmp_path):
    assert registry_path() != Path.home() / ".config" / "coderstanding" / "apps.json"
    assert Path(os.environ[REGISTRY_ENV]).is_relative_to(tmp_path)
    assert Path(os.environ[DATA_HOME_ENV]).is_relative_to(tmp_path)
