from typer.testing import CliRunner

from optionda.cli import app
from optionda.shellenv import render_shellenv

runner = CliRunner()


def test_render_bash_hook() -> None:
    script = render_shellenv("bash")
    assert "OPTIONDA_SHELL_HOOK=1" in script
    assert "OPTIONDA_ACTIVE" in script
    assert "[optionda]" in script
    assert "activate)" in script
    assert "deactivate)" in script
    assert r"\e[36m" in script


def test_current_is_session_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0

    cur = runner.invoke(app, ["current"])
    assert cur.exit_code == 0
    assert cur.stdout.strip() == ""

    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    cur2 = runner.invoke(app, ["current"])
    assert cur2.stdout.strip() == "demo"

    env = runner.invoke(app, ["shellenv"])
    assert env.exit_code == 0
    assert "optionda activate" in env.stdout or "activate)" in env.stdout
