from __future__ import annotations

import os
from pathlib import Path

_BUNDLE_KEYS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


def _windows_conda_bundle(environ: dict[str, str]) -> str | None:
    prefix = environ.get("CONDA_PREFIX")
    if not prefix:
        return None
    candidate = Path(prefix) / "Library" / "ssl" / "cacert.pem"
    if candidate.is_file():
        return str(candidate)
    return None


def _certifi_bundle() -> str | None:
    try:
        import certifi
    except ImportError:
        return None
    path = certifi.where()
    if path and Path(path).is_file():
        return path
    return None


def _replacement_bundle(environ: dict[str, str]) -> str | None:
    return _windows_conda_bundle(environ) or _certifi_bundle()


def sanitize_ssl_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Drop or rewrite conda Git Bash SSL paths that point at missing files.

    On Windows, ``conda activate`` often sets ``SSL_CERT_FILE`` to
    ``$CONDA_PREFIX/ssl/cacert.pem`` (Unix layout). The real bundle lives at
    ``$CONDA_PREFIX/Library/ssl/cacert.pem``. httpx then fails with
    ``[Errno 2] No such file or directory``.
    """
    env = os.environ if environ is None else environ
    replacement = _replacement_bundle(env)
    changed: dict[str, str] = {}
    for key in _BUNDLE_KEYS:
        raw = env.get(key)
        if not raw:
            continue
        if Path(raw).is_file():
            continue
        if replacement:
            env[key] = replacement
            changed[key] = replacement
        else:
            del env[key]
            changed[key] = ""
    cert_dir = env.get("SSL_CERT_DIR")
    if cert_dir and not Path(cert_dir).is_dir():
        del env["SSL_CERT_DIR"]
        changed["SSL_CERT_DIR"] = ""
    return changed
