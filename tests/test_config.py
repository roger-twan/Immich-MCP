import pytest

from immich_mcp.config import Config


@pytest.mark.parametrize("url", ["http://localhost:2283", "http://localhost:2283/api/"])
def test_config_normalizes_api_url(monkeypatch: pytest.MonkeyPatch, tmp_path, url: str) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"IMMICH_URL={url}\nIMMICH_API_KEY= secret \n")

    assert Config.from_dotenv() == Config("http://localhost:2283/api", "secret")


@pytest.mark.parametrize("missing", ["IMMICH_URL", "IMMICH_API_KEY"])
def test_config_requires_both_values(monkeypatch: pytest.MonkeyPatch, tmp_path, missing: str) -> None:
    monkeypatch.chdir(tmp_path)
    values = {"IMMICH_URL": "http://localhost:2283", "IMMICH_API_KEY": "secret"}
    values.pop(missing)
    (tmp_path / ".env").write_text("".join(f"{key}={value}\n" for key, value in values.items()))

    with pytest.raises(ValueError, match=missing):
        Config.from_dotenv()


@pytest.mark.parametrize("url", ["localhost:2283", "https://user:pass@example.com", "https://example.com/?key=secret"])
def test_config_rejects_invalid_url(monkeypatch: pytest.MonkeyPatch, tmp_path, url: str) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"IMMICH_URL={url}\nIMMICH_API_KEY=secret\n")

    with pytest.raises(ValueError, match="IMMICH_URL"):
        Config.from_dotenv()


def test_config_ignores_process_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMMICH_URL=http://file-host:2283\nIMMICH_API_KEY=file-secret\n")
    monkeypatch.setenv("IMMICH_URL", "http://env-host:2283")
    monkeypatch.setenv("IMMICH_API_KEY", "env-secret")

    assert Config.from_dotenv() == Config("http://file-host:2283/api", "file-secret")


def test_config_requires_dotenv_even_when_process_values_exist(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IMMICH_URL", "http://env-host:2283")
    monkeypatch.setenv("IMMICH_API_KEY", "env-secret")

    with pytest.raises(ValueError, match="IMMICH_URL"):
        Config.from_dotenv()


def test_config_does_not_interpolate_process_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMMICH_URL=http://localhost:2283\nIMMICH_API_KEY=${IMMICH_API_KEY}\n")
    monkeypatch.setenv("IMMICH_API_KEY", "env-secret")

    assert Config.from_dotenv().api_key == "${IMMICH_API_KEY}"
