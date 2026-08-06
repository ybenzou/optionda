from __future__ import annotations

from pathlib import Path

# Session activation via OPTIONDA_ACTIVE (like conda activate).
# Prompt uses cyan [brackets] to avoid clashing with conda/venv (parens).
BASH_HOOK = r'''# optionda shell hook — eval "$(optionda shellenv)"
export OPTIONDA_SHELL_HOOK=1

if [ -z "${__OPTIONDA_PS1_SAVED+x}" ]; then
  __OPTIONDA_PS1_SAVED="$PS1"
fi

__optionda_update_prompt() {
  # cyan [name] — distinct from (.venv) / (base)
  if [ -n "${OPTIONDA_ACTIVE:-}" ]; then
    PS1="\[\e[36m\][${OPTIONDA_ACTIVE}]\[\e[0m\] ${__OPTIONDA_PS1_SAVED}"
  else
    PS1="\[\e[36m\][optionda]\[\e[0m\] ${__OPTIONDA_PS1_SAVED}"
  fi
}

optionda() {
  local __cmd="${1:-}"
  case "$__cmd" in
    activate)
      shift
      local __name="${1:-}"
      if [ -z "$__name" ]; then
        echo "usage: optionda activate <account>" >&2
        return 2
      fi
      if ! command optionda assert-account "$__name"; then
        return 1
      fi
      export OPTIONDA_ACTIVE="$__name"
      __optionda_update_prompt
      printf '\033[36mactivated [%s]\033[0m\n' "$__name"
      ;;
    deactivate)
      unset OPTIONDA_ACTIVE
      __optionda_update_prompt
      printf '\033[36mdeactivated → [optionda]\033[0m\n'
      ;;
    *)
      command optionda "$@"
      local __optionda_ret=$?
      __optionda_update_prompt
      return $__optionda_ret
      ;;
  esac
}

__optionda_update_prompt
'''

BEGIN_MARK = "# >>> optionda initialize >>>"
END_MARK = "# <<< optionda initialize <<<"

RC_BLOCK = f"""{BEGIN_MARK}
# Managed by `optionda init` (like conda init). Remove with: optionda init --reverse
# Safe when optionda is not on PATH (e.g. conda base): no error, loads later if available.
__optionda_maybe_init() {{
  if [ -n "${{OPTIONDA_SHELL_HOOK:-}}" ]; then
    return 0
  fi
  if command -v optionda >/dev/null 2>&1; then
    eval "$(command optionda shellenv)"
  fi
}}
__optionda_maybe_init
if [ -n "${{ZSH_VERSION:-}}" ]; then
  if typeset -f add-zsh-hook >/dev/null 2>&1 || autoload -Uz add-zsh-hook 2>/dev/null; then
    add-zsh-hook precmd __optionda_maybe_init 2>/dev/null || true
  elif [[ " ${{precmd_functions[*]-}} " != *" __optionda_maybe_init "* ]]; then
    precmd_functions+=(__optionda_maybe_init)
  fi
else
  case "${{PROMPT_COMMAND:-}}" in
    *__optionda_maybe_init*) ;;
    "") PROMPT_COMMAND=__optionda_maybe_init ;;
    *) PROMPT_COMMAND="__optionda_maybe_init;${{PROMPT_COMMAND}}" ;;
  esac
fi
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
    bashrc = home / ".bashrc"
    bash_profile = home / ".bash_profile"
    # Write both-friendly: prefer .bashrc; if only profile exists use it
    if bashrc.exists() or not bash_profile.exists():
        return bashrc
    return bash_profile


def rc_has_hook(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return BEGIN_MARK in text and "optionda shellenv" in text


def install_rc_hook(path: Path) -> str:
    """Install or refresh managed hook block. Returns added|updated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    if BEGIN_MARK in text and END_MARK in text:
        remove_rc_hook(path)
        text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        status = "updated"
    else:
        status = "added"
    addition = ("\n" if text and not text.endswith("\n") else "") + RC_BLOCK
    path.write_text((text if text.endswith("\n") or not text else text + "\n") + addition, encoding="utf-8")
    return status


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
    new = text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")
    if new.strip():
        path.write_text(new if new.endswith("\n") else new + "\n", encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")
    return "removed"
