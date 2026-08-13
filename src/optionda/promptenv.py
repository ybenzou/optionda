from __future__ import annotations

import os
import sys
from pathlib import Path

BEGIN = "# >>> optionda prompt >>>"
END = "# <<< optionda prompt <<<"

# conda+Git Bash on Windows points SSL_CERT_FILE at a missing Unix-layout bundle.
SSL_FIX_SNIPPET = r"""__optionda_fix_ssl_cert() {
  local key path fallback
  fallback=""
  if [ -n "${CONDA_PREFIX:-}" ] && [ -f "${CONDA_PREFIX}/Library/ssl/cacert.pem" ]; then
    fallback="${CONDA_PREFIX}/Library/ssl/cacert.pem"
  fi
  for key in SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE; do
    eval "path=\${$key-}"
    if [ -n "$path" ] && [ ! -f "$path" ]; then
      if [ -n "$fallback" ]; then
        export "$key=$fallback"
      else
        unset "$key"
      fi
    fi
  done
}
__optionda_fix_ssl_cert
"""

# Reads <data>/active each prompt. Lives in venv/conda activate only — never ~/.bashrc.
PROMPT_SNIPPET = f"""{BEGIN}
# optionda prompt (venv/conda scoped — not a global shell hook)
{SSL_FIX_SNIPPET}__optionda_ps1_refresh() {{
  local root name
  if [ -n "${{OPTIONDA_HOME:-}}" ]; then
    root="$OPTIONDA_HOME"
  elif [ -n "${{VIRTUAL_ENV:-}}" ]; then
    root="$VIRTUAL_ENV/share/optionda"
  elif [ -n "${{CONDA_PREFIX:-}}" ]; then
    root="$CONDA_PREFIX/share/optionda"
  else
    root="$HOME/.optionda"
  fi
  name=""
  if [ -f "$root/active" ]; then
    name=$(head -n 1 "$root/active" | tr -d '\\r\\n')
  fi
  if [ -z "${{__OPTIONDA_BASE_PS1+x}}" ]; then
    __OPTIONDA_BASE_PS1="$PS1"
  fi
  if [ -n "$name" ]; then
    PS1="\\[\\e[36m\\][${{name}}]\\[\\e[0m\\] ${{__OPTIONDA_BASE_PS1}}"
  else
    PS1="\\[\\e[36m\\][optionda]\\[\\e[0m\\] ${{__OPTIONDA_BASE_PS1}}"
  fi
  printf '\\033]0;%s\\007' "${{name:-optionda}}"
}}
case "${{PROMPT_COMMAND:-}}" in
  *__optionda_ps1_refresh*) ;;
  "") PROMPT_COMMAND=__optionda_ps1_refresh ;;
  *) PROMPT_COMMAND="__optionda_ps1_refresh;${{PROMPT_COMMAND}}" ;;
esac
__optionda_ps1_refresh
{END}
"""


def set_terminal_title(title: str) -> None:
    """Best-effort tab/window title — no shell config required."""
    text = (title or "optionda").replace("\x1b", "").replace("\x07", "")
    try:
        if sys.stdout.isatty():
            sys.stdout.write(f"\033]0;{text}\007")
            sys.stdout.flush()
    except OSError:
        pass


def render_prompt_apply() -> str:
    """Shell code for the *current* session: eval \"$(optionda prompt apply)\"."""
    # Same logic as PROMPT_SNIPPET, without the mark comments (safe to re-eval).
    return SSL_FIX_SNIPPET + """__optionda_ps1_refresh() {
  local root name
  if [ -n "${OPTIONDA_HOME:-}" ]; then
    root="$OPTIONDA_HOME"
  elif [ -n "${VIRTUAL_ENV:-}" ]; then
    root="$VIRTUAL_ENV/share/optionda"
  elif [ -n "${CONDA_PREFIX:-}" ]; then
    root="$CONDA_PREFIX/share/optionda"
  else
    root="$HOME/.optionda"
  fi
  name=""
  if [ -f "$root/active" ]; then
    name=$(head -n 1 "$root/active" | tr -d '\\r\\n')
  fi
  if [ -z "${__OPTIONDA_BASE_PS1+x}" ]; then
    __OPTIONDA_BASE_PS1="$PS1"
  fi
  if [ -n "$name" ]; then
    PS1="\\[\\e[36m\\][${name}]\\[\\e[0m\\] ${__OPTIONDA_BASE_PS1}"
  else
    PS1="\\[\\e[36m\\][optionda]\\[\\e[0m\\] ${__OPTIONDA_BASE_PS1}"
  fi
  printf '\\033]0;%s\\007' "${name:-optionda}"
}
case "${PROMPT_COMMAND:-}" in
  *__optionda_ps1_refresh*) ;;
  "") PROMPT_COMMAND=__optionda_ps1_refresh ;;
  *) PROMPT_COMMAND="__optionda_ps1_refresh;${PROMPT_COMMAND}" ;;
esac
__optionda_ps1_refresh
"""


def resolve_venv_activate() -> Path | None:
    """Return POSIX activate script for the current venv, if any."""
    root = os.environ.get("VIRTUAL_ENV")
    if not root:
        return None
    base = Path(root)
    for candidate in (base / "Scripts" / "activate", base / "bin" / "activate"):
        if candidate.is_file():
            return candidate
    return None


def resolve_conda_activate_d() -> Path | None:
    prefix = os.environ.get("CONDA_PREFIX")
    if not prefix:
        return None
    return Path(prefix) / "etc" / "conda" / "activate.d" / "optionda_prompt.sh"


def prompt_installed_in(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return BEGIN in text and END in text


def install_prompt(path: Path) -> str:
    """Install or refresh prompt block. Returns added|updated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    if BEGIN in text and END in text:
        uninstall_prompt(path)
        text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        status = "updated"
    else:
        status = "added"
    addition = ("\n" if text and not text.endswith("\n") else "") + PROMPT_SNIPPET
    if text and not text.endswith("\n"):
        text = text + "\n"
    path.write_text(text + addition, encoding="utf-8")
    return status


def uninstall_prompt(path: Path) -> str:
    if not path.exists():
        return "absent"
    text = path.read_text(encoding="utf-8", errors="ignore")
    if BEGIN not in text:
        return "absent"
    start = text.find(BEGIN)
    end = text.find(END)
    if start < 0 or end < 0:
        return "absent"
    end += len(END)
    new = text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")
    path.write_text(new if new.endswith("\n") or not new else new + "\n", encoding="utf-8")
    return "removed"


def install_current_env_prompt(*, prefer: str = "auto") -> tuple[str, Path]:
    """Install into venv activate and/or conda activate.d.

    prefer: auto | venv | conda
    auto = venv if VIRTUAL_ENV set, else conda.
    """
    mode = (prefer or "auto").strip().lower()
    venv_act = resolve_venv_activate()
    conda_path = resolve_conda_activate_d()

    if mode == "venv":
        if venv_act is None:
            raise RuntimeError("no VIRTUAL_ENV — activate a venv first")
        return install_prompt(venv_act), venv_act
    if mode == "conda":
        if conda_path is None:
            raise RuntimeError("no CONDA_PREFIX — conda activate <env> first")
        return install_prompt(conda_path), conda_path

    # auto
    if venv_act is not None:
        return install_prompt(venv_act), venv_act
    if conda_path is not None:
        return install_prompt(conda_path), conda_path
    raise RuntimeError(
        "no active venv/conda env — activate your environment first, then: "
        "optionda prompt install"
    )


def uninstall_current_env_prompt(*, prefer: str = "auto") -> tuple[str, Path | None]:
    mode = (prefer or "auto").strip().lower()
    venv_act = resolve_venv_activate()
    conda_path = resolve_conda_activate_d()

    if mode == "venv":
        if venv_act is None:
            return "absent", None
        return uninstall_prompt(venv_act), venv_act
    if mode == "conda":
        if conda_path is None:
            return "absent", None
        return uninstall_prompt(conda_path), conda_path

    if venv_act is not None and prompt_installed_in(venv_act):
        return uninstall_prompt(venv_act), venv_act
    if conda_path is not None and prompt_installed_in(conda_path):
        return uninstall_prompt(conda_path), conda_path
    if venv_act is not None:
        return "absent", venv_act
    if conda_path is not None:
        return "absent", conda_path
    return "absent", None
