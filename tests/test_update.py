from optionda.updater import compare_versions, plan_update


def test_compare_versions() -> None:
    assert compare_versions("1.2.0", "1.1.4") == 1
    assert compare_versions("1.2.0", "1.2.0") == 0
    assert compare_versions("1.1.9", "1.2.0") == -1


def test_plan_update_current_is_ok() -> None:
    action, message = plan_update("1.2.0", "1.2.0")
    assert action == "ok"
    assert "1.2.0" in message


def test_plan_update_newer_installs() -> None:
    action, message = plan_update("1.2.0", "1.2.1")
    assert action == "upgrade"
    assert "1.2.1" in message


def test_fetch_pypi_version_reads_info(monkeypatch) -> None:
    from optionda.updater import fetch_pypi_version

    class FakeResponse:
        def raise_for_status(self):
            return self

        def json(self):
            return {"info": {"version": "1.2.0"}}

    monkeypatch.setattr(
        "optionda.updater.httpx.get",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    assert fetch_pypi_version() == "1.2.0"
