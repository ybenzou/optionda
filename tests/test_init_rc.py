from pathlib import Path

from typer.testing import CliRunner

from optionda.cli import app
from optionda.shellenv import (
    RC_BLOCK,
    install_rc_hook,
    rc_has_hook,
    remove_rc_hook,
)

runner = CliRunner()


def test_install_and_refresh(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("# existing\n", encoding="utf-8")
    assert install_rc_hook(rc) == "added"
    assert rc_has_hook(rc)
    assert install_rc_hook(rc) == "updated"
    text = rc.read_text(encoding="utf-8")
    assert text.count("optionda shellenv") == 1


def test_remove_hook(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    install_rc_hook(rc)
    assert remove_rc_hook(rc) == "removed"
    assert not rc_has_hook(rc)
    assert remove_rc_hook(rc) == "absent"


def test_rc_block_is_safe_without_optionda_on_path() -> None:
    assert "command -v optionda" in RC_BLOCK
    assert "__optionda_maybe_init" in RC_BLOCK
    assert 'eval "$(optionda shellenv)"' not in RC_BLOCK
    assert 'eval "$(command optionda shellenv)"' in RC_BLOCK


def test_init_only_removes_hook(tmp_path: Path, monkeypatch) -> None:
    rc = tmp_path / ".bashrc"
    install_rc_hook(rc)
    assert rc_has_hook(rc)
    monkeypatch.setattr("optionda.cli.default_rc_path", lambda shell="bash": rc)
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path / "data"))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert "removed leftover" in result.output
    assert not rc_has_hook(rc)
    # Second run installs nothing
    result2 = runner.invoke(app, ["init"])
    assert result2.exit_code == 0
    assert "nothing to change" in result2.output
    assert not rc_has_hook(rc)
