from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_orchestrator():
    orc = MagicMock()
    orc.get_capability_instance.return_value = None
    orc.get_children.return_value = []
    return orc


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agents" / "test_agent"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def agent_yml(agent_dir: Path) -> Path:
    path = agent_dir / "agent.yml"
    path.write_text("name: TestAgent\nid: test-uuid-1234\n")
    return path


@pytest.fixture
def agent_with_full_config(agent_dir: Path) -> Path:
    path = agent_dir / "agent.yml"
    path.write_text(
        "name: TestAgent\n"
        "id: test-uuid-1234\n"
        "master_id: master-uuid\n"
        "autostart: true\n"
        "conversational: true\n"
        "roles:\n  - chat\n"
    )
    return path
