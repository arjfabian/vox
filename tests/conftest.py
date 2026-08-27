from pathlib import Path

import pytest


@pytest.fixture
def mock_logger():
    from unittest.mock import MagicMock

    return MagicMock()


@pytest.fixture
def mock_orchestrator():
    from unittest.mock import MagicMock

    orc = MagicMock()
    orc.get_capability_instance.return_value = None
    orc.get_children.return_value = []
    return orc


@pytest.fixture
def persona_dir(tmp_path: Path) -> Path:
    d = tmp_path / "instance" / "personas" / "test_workload"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def manifest_yml(persona_dir: Path) -> Path:
    path = persona_dir / "manifest.yml"
    path.write_text("name: TestWorkload\nid: test-uuid-1234\n")
    return path


@pytest.fixture
def workload_with_full_config(persona_dir: Path) -> Path:
    path = persona_dir / "manifest.yml"
    path.write_text(
        "name: TestWorkload\n"
        "id: test-uuid-1234\n"
        "master_id: master-uuid\n"
        "autostart: true\n"
        "conversational: true\n"
        "roles:\n  - chat\n"
    )
    return path
