from pathlib import Path

from optionda.shellenv import install_rc_hook, rc_has_hook, remove_rc_hook


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
