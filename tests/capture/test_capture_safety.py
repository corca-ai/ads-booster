import os
from pathlib import Path

import pytest

from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    UdidCaptureLeaseFactory,
    path_has_symlink_component,
)
from ads_booster.contracts import ErrorCode


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        (Path(os.sep, "var"), Path(os.sep, "private", "var")),
        (Path(os.sep, "tmp"), Path(os.sep, "private", "tmp")),
    ],
)
def test_path_safety_when_macos_standard_alias_is_used(
    alias: Path,
    canonical: Path,
) -> None:
    # Given a standard macOS filesystem alias rather than a user-controlled redirect
    if not alias.is_symlink() or alias.resolve() != canonical:
        pytest.skip("macOS standard alias is unavailable on this host")

    # When a capture path is checked below that alias
    result = path_has_symlink_component(alias / "trace-marketing" / "output.png")

    # Then the trusted system alias is accepted while explicit workspace symlinks remain rejected
    assert result is False


def test_capture_control_when_cancel_marker_exists_then_rejects(tmp_path: Path) -> None:
    cancel_file = tmp_path / "cancel"
    _ = cancel_file.touch()
    control = CaptureControl.start(timeout_seconds=30, cancel_file=cancel_file)

    with pytest.raises(CaptureAdapterError) as raised:
        control.checkpoint()

    assert raised.value.code is ErrorCode.CAPTURE_CANCELLED


def test_udid_lease_when_same_simulator_is_already_captured(tmp_path: Path) -> None:
    lease_factory = UdidCaptureLeaseFactory(root=tmp_path / "leases")
    udid = "E1FB798D-79E6-4B25-A987-D298A4FD122A"

    with (
        lease_factory.acquire(udid),
        pytest.raises(CaptureAdapterError) as raised,
        lease_factory.acquire(udid),
    ):
        pass

    assert raised.value.code is ErrorCode.CAPTURE_LEASE_UNAVAILABLE
    with lease_factory.acquire(udid):
        pass
