from pathlib import Path

from app.config import PROJECT_ROOT, Settings


def test_relative_storage_settings_resolve_from_project_root():
    settings = Settings(
        database_url="sqlite:///./data/test.db",
        artifact_root=Path("data/test-artifacts"),
        raw_data_root=Path("data/test-raw"),
    )
    assert settings.database_url == f"sqlite:///{PROJECT_ROOT / 'data/test.db'}"
    assert settings.artifact_root == PROJECT_ROOT / "data/test-artifacts"
    assert settings.raw_data_root == PROJECT_ROOT / "data/test-raw"
