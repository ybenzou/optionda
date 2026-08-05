from __future__ import annotations

from pathlib import Path

BASH_HOOK = r'''# optionda shell hook — eval "$(optionda shellenv)"
export OPTIONDA_SHELL_HOOK=1

if [ -z "${__OPTIONDA_PS1_SAVED+x}" ]; then
  __OPTIONDA_PS1_SAVED="$PS1"
fi

__optionda_update_prompt() {
  local acc
  acc="$(command optionda current 2>/dev/null)" || acc=""
  if [ -n "$acc" ]; then
    PS1="(${acc}) ${__OPTIONDA_PS1_SAVED}"
  else
    PS1="${__OPTIONDA_PS1_SAVED}"
  fi
}

optionda() {
  command optionda "$@"
  local __optionda_ret=$?
  __optionda_update_prompt
  return $__optionda_ret
}

__optionda_update_prompt
'''

BEGIN_MARK = "# >>> optionda initialize >>>"
END_MARK = "# <<< optionda initialize <<<"

RC_BLOCK = f"""{BEGIN_MARK}
# Managed by `optionda init` (like conda init). Remove with: optionda init --reverse
eval "$(optionda shellenv)"
{END_MARK}
"""


def render_shellenv(shell: str = "bash") -> str:
    name = shell.strip().lower()
    if name in {"bash", "zsh", "sh", "gitbash", "git-bash"}:
        return BASH_HOOK
    raise ValueError(f"unsupported shell: {shell} (supported: bash, zsh)")


def default_rc_path(shell: str = "bash") -> Path:
    name = shell.strip().lower()
    home = Path.home()
    if name in {"zsh"}:
        return home / ".zshrc"
    # Git Bash / bash on Windows and Unix
    bashrc = home / ".bashrc"
    bash_profile = home / ".bash_profile"
    # Prefer .bashrc; create it if neither exists
    if bashrc.exists() or not bash_profile.exists():
        return bashrc
    return bash_profile


def rc_has_hook(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return BEGIN_MARK in text and "optionda shellenv" in text


def install_rc_hook(path: Path) -> str:
    """Idempotently install hook block. Returns 'added' | 'unchanged'."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="ignore")
    else:
        text = ""
    if BEGIN_MARK in text and END_MARK in text:
        return "unchanged"
    addition = ("\n" if text and not text.endswith("\n") else "") + RC_BLOCK
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + addition, encoding="utf-8")
    return "added"


def remove_rc_hook(path: Path) -> str:
    """Remove managed block. Returns 'removed' | 'absent'."""
    if not path.exists():
        return "absent"
    text = path.read_text(encoding="utf-8", errors="ignore")
    if BEGIN_MARK not in text:
        return "absent"
    start = text.find(BEGIN_MARK)
    end = text.find(END_MARK)
    if start < 0 or end < 0:
        return "absent"
    end += len(END_MARK)
    # drop surrounding newlines cleanly
    new = text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")
    if new.strip():
        path.write_text(new if new.endswith("\n") else new + "\n", encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")
    return "removed"
