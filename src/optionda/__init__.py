"""optionda — terminal options desk (MODEL marks, frozen IV)."""

__version__ = "1.0.20"


def _claim_windows_identity() -> None:
    import sys

    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "yuanben.optionda.desk"
        )
    except Exception:  # noqa: BLE001
        return


_claim_windows_identity()
