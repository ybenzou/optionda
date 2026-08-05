from typer.testing import CliRunner

from optionda.cli import app
from optionda.shellenv import render_shellenv

runner = CliRunner()


def test_render_bash_hook() -> None:
    script = render_shellenv("bash")
    assert "OPTIONDA_SHELL_HOOK=1" in script
    assert "__optionda_update_prompt" in script
    assert "optionda current" in script


def test_current_and_shellenv(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0
    cur = runner.invoke(app, ["current"])
    assert cur.exit_code == 0
    assert cur.stdout.strip() == "demo"

    assert runner.invoke(app, ["create", "hedge"]).exit_code == 0
    assert runner.invoke(app, ["use", "hedge"]).exit_code == 0
    cur2 = runner.invoke(app, ["current"])
    assert cur2.stdout.strip() == "hedge"

    env = runner.invoke(app, ["shellenv"])
    assert env.exit_code == 0
    assert "OPTIONDA_SHELL_HOOK=1" in env.stdout
    assert "__optionda_update_prompt" in env.stdout
    assert "PS1=" in env.stdout
