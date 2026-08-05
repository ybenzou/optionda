from __future__ import annotations

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


def render_shellenv(shell: str = "bash") -> str:
    name = shell.strip().lower()
    if name in {"bash", "zsh", "sh", "gitbash", "git-bash"}:
        return BASH_HOOK
    raise ValueError(f"unsupported shell: {shell} (supported: bash, zsh)")
