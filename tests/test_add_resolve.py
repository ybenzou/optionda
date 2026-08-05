from pathlib import Path

import pytest

from optionda.add_resolve import resolve_add_lines


def test_single_occ() -> None:
    assert resolve_add_lines(["AAPL261120C00350000"]) == ["AAPL261120C00350000"]


def test_human_argv() -> None:
    assert resolve_add_lines(["INTC", "261016", "140", "C"]) == [
        "INTC261016C00140000"
    ]


def test_multi_occ() -> None:
    lines = resolve_add_lines(
        ["INTC261016C00140000", "TSLA261218C00500000"]
    )
    assert lines == ["INTC261016C00140000", "TSLA261218C00500000"]


def test_file(tmp_path: Path) -> None:
    path = tmp_path / "pos.txt"
    path.write_text("INTC 261016 140 C\nTSLA 261218 500 C\n", encoding="utf-8")
    lines = resolve_add_lines([str(path)])
    assert "INTC261016C00140000" in lines[0] or lines[0].startswith("INTC")
    assert len(lines) == 2
