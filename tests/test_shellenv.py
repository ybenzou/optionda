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
    assert "command optionda" in script


def test_activate_persists_without_shell_hook(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.delenv("OPTIONDA_ACTIVE", raising=False)
    monkeypatch.delenv("OPTIONDA_SHELL_HOOK", raising=False)
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0

    cur = runner.invoke(app, ["current"])
    assert cur.exit_code == 0
    assert cur.stdout.strip() == ""

    act = runner.invoke(app, ["activate", "demo"])
    assert act.exit_code == 0, act.output
    assert "activated demo" in act.output

    cur2 = runner.invoke(app, ["current"])
    assert cur2.stdout.strip() == "demo"
    assert (tmp_path / "active").read_text(encoding="utf-8").strip() == "demo"

    de = runner.invoke(app, ["deactivate"])
    assert de.exit_code == 0
    assert runner.invoke(app, ["current"]).stdout.strip() == ""
