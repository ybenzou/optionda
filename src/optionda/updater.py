from __future__ import annotations

import subprocess
import sys
from typing import Literal

import httpx


def compare_versions(current: str, latest: str) -> int:
    def parts(value: str) -> tuple[int, int, int]:
        bits = (value or "0").split(".")
        nums = []
        for item in bits[:3]:
            try:
                nums.append(int(item))
            except ValueError:
                nums.append(0)
        while len(nums) < 3:
            nums.append(0)
        return nums[0], nums[1], nums[2]

    left, right = parts(current), parts(latest)
    if left > right:
        return 1
    if left < right:
        return -1
    return 0


def plan_update(current: str, latest: str) -> tuple[Literal["ok", "upgrade"], str]:
    if compare_versions(current, latest) >= 0:
        return "ok", f"ok {current}"
    return "upgrade", f"upgrade {current} → {latest}"


def fetch_pypi_version() -> str:
    data = httpx.get("https://pypi.org/pypi/optionda/json", timeout=15).raise_for_status().json()
    version = str((data.get("info") or {}).get("version") or "").strip()
    if not version:
        raise RuntimeError("PyPI did not return optionda version")
    return version


def run_upgrade(latest: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", f"optionda=={latest}"],
        check=True,
    )
