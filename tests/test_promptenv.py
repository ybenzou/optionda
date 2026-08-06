from pathlib import Path

from optionda.promptenv import (
    BEGIN,
    END,
    install_prompt,
    prompt_installed_in,
    uninstall_prompt,
)


def test_install_and_uninstall_prompt_block(tmp_path: Path) -> None:
    activate = tmp_path / "activate"
    activate.write_text("# venv activate\nPS1='(venv) '\n", encoding="utf-8")
    assert install_prompt(activate) == "added"
    assert prompt_installed_in(activate)
    text = activate.read_text(encoding="utf-8")
    assert BEGIN in text and END in text
    assert "__optionda_ps1_refresh" in text
    assert text.count(BEGIN) == 1

    assert install_prompt(activate) == "updated"
    assert activate.read_text(encoding="utf-8").count(BEGIN) == 1

    assert uninstall_prompt(activate) == "removed"
    assert not prompt_installed_in(activate)
    left = activate.read_text(encoding="utf-8")
    assert BEGIN not in left
    assert "PS1='(venv) '" in left
