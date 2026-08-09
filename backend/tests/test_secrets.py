from types import SimpleNamespace

from app.services import secrets
from app.services.secrets import SecretStore


def test_secret_store_reads_api_key_loaded_from_dotenv(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "get_settings", lambda: SimpleNamespace(openai_api_key=" dotenv-key "))
    monkeypatch.setattr(secrets.keyring, "get_password", lambda *_args: None)

    assert SecretStore.get_api_key() == "dotenv-key"
    assert SecretStore.is_configured() is True
