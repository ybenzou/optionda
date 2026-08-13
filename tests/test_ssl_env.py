from pathlib import Path

from optionda.ssl_env import sanitize_ssl_env


def test_sanitize_ssl_env_rewrites_missing_conda_unix_bundle(tmp_path: Path, monkeypatch) -> None:
    prefix = tmp_path / "envs" / "optionda"
    missing = prefix / "ssl" / "cacert.pem"
    windows = prefix / "Library" / "ssl" / "cacert.pem"
    windows.parent.mkdir(parents=True)
    windows.write_text("bundle", encoding="utf-8")
    env = {
        "CONDA_PREFIX": str(prefix),
        "SSL_CERT_FILE": str(missing),
    }

    changed = sanitize_ssl_env(env)

    assert missing.is_file() is False
    assert env["SSL_CERT_FILE"] == str(windows)
    assert changed["SSL_CERT_FILE"] == str(windows)


def test_sanitize_ssl_env_falls_back_to_certifi_without_conda(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "gone.pem"
    fake = tmp_path / "certifi.pem"
    fake.write_text("certifi", encoding="utf-8")
    monkeypatch.setattr("optionda.ssl_env._certifi_bundle", lambda: str(fake))
    env = {"SSL_CERT_FILE": str(missing)}

    changed = sanitize_ssl_env(env)

    assert env["SSL_CERT_FILE"] == str(fake)
    assert changed["SSL_CERT_FILE"] == str(fake)


def test_sanitize_ssl_env_unsets_when_no_replacement(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("optionda.ssl_env._certifi_bundle", lambda: None)
    env = {"SSL_CERT_FILE": str(tmp_path / "gone.pem")}

    changed = sanitize_ssl_env(env)

    assert "SSL_CERT_FILE" not in env
    assert changed["SSL_CERT_FILE"] == ""


def test_sanitize_ssl_env_keeps_valid_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "cacert.pem"
    bundle.write_text("ok", encoding="utf-8")
    env = {"SSL_CERT_FILE": str(bundle)}

    changed = sanitize_ssl_env(env)

    assert env["SSL_CERT_FILE"] == str(bundle)
    assert changed == {}
