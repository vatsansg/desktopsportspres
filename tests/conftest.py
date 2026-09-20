import pytest

from ledsync.config import Config


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_dir=tmp_path)
