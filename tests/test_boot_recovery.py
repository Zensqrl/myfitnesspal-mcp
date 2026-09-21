import runpy
from pathlib import Path


needs_recovery = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/recover-gateway.py")
)["needs_recovery"]


def test_boot_address_failure_is_retried():
    assert needs_recovery({"Status": "exited", "Error": "bind: cannot assign requested address"})


def test_manual_stop_and_other_failures_are_not_restarted():
    for state in ({"Status": "exited", "Error": ""},
                  {"Status": "exited", "Error": "address already in use"},
                  {"Status": "running", "Error": "cannot assign requested address"}):
        assert not needs_recovery(state)
