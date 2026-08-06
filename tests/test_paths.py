from pathlib import Path

from optionda.paths import ensure_home, resolve_home, resolve_home_info, user_home


def test_optionda_home_override_wins(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path / "override"))
    monkeypatch.setenv("CONDA_PREFIX", str(tmp_path / "conda"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "venv"))
    info = resolve_home_info()
    assert info.mode == "override"
    assert info.path == tmp_path / "override"


def test_conda_env_isolates(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPTIONDA_HOME", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    desk = tmp_path / "miniconda" / "envs" / "desk"
    (desk / "conda-meta").mkdir(parents=True)
    monkeypatch.setenv("CONDA_PREFIX", str(desk))
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "desk")
    # Pretend the process itself is not an isolated interpreter.
    monkeypatch.setattr("optionda.paths.sys.prefix", str(tmp_path / "system"))
    monkeypatch.setattr("optionda.paths.sys.base_prefix", str(tmp_path / "system"))
    info = resolve_home_info()
    assert info.mode == "env"
    assert info.env_kind == "conda"
    assert info.env_name == "desk"
    assert info.path == desk / "share" / "optionda"


def test_venv_isolates(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPTIONDA_HOME", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))
    monkeypatch.setattr("optionda.paths.sys.prefix", str(tmp_path / "system"))
    monkeypatch.setattr("optionda.paths.sys.base_prefix", str(tmp_path / "system"))
    info = resolve_home_info()
    assert info.mode == "env"
    assert info.env_kind == "venv"
    assert info.path == tmp_path / ".venv" / "share" / "optionda"


def test_venv_wins_over_conda_base(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPTIONDA_HOME", raising=False)
    monkeypatch.setenv("CONDA_PREFIX", str(tmp_path / "miniconda"))
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "base")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "project" / ".venv"))
    info = resolve_home_info()
    assert info.env_kind == "venv"
    assert info.path == tmp_path / "project" / ".venv" / "share" / "optionda"


def test_sys_prefix_isolates_without_activate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPTIONDA_HOME", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("CONDA_PREFIX", str(tmp_path / "miniconda"))  # stale base
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "base")
    fake = tmp_path / "project" / ".venv"
    fake.mkdir(parents=True)
    monkeypatch.setattr("optionda.paths.sys.prefix", str(fake))
    monkeypatch.setattr("optionda.paths.sys.base_prefix", str(tmp_path / "py"))
    info = resolve_home_info()
    assert info.mode == "env"
    assert info.path == fake / "share" / "optionda"


def test_user_home_when_no_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPTIONDA_HOME", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    system = tmp_path / "system-py"
    monkeypatch.setattr("optionda.paths.sys.prefix", str(system))
    monkeypatch.setattr("optionda.paths.sys.base_prefix", str(system))
    info = resolve_home_info()
    assert info.mode == "user"
    assert info.path == user_home()
    assert resolve_home() == user_home()


def test_ensure_home_creates_dirs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path / "lib"))
    root = ensure_home()
    assert (root / "accounts").is_dir()
    assert (root / "books").is_dir()
    assert (root / "logs").is_dir()
    assert (root / "surfaces").is_dir()
