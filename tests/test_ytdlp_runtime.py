from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from transpodcast.adapters.ytdlp_runtime import YtDlpRuntime


class FakeInstaller:
    """Stands in for venv, pip, and ``yt-dlp --version``."""

    def __init__(self, *, fail_pip: bool = False) -> None:
        self.fail_pip = fail_pip
        self.commands: list[list[str]] = []

    def __call__(
        self, command: list[str], **_kwargs
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[1:3] == ["-m", "venv"]:
            (Path(command[3]) / "bin").mkdir(parents=True)
            (Path(command[3]) / "bin" / "python").write_text("python")
        elif command[1:4] == ["-m", "pip", "install"]:
            if self.fail_pip:
                return subprocess.CompletedProcess(command, 1, "", "no network")
            version = command[-1].split("==")[1]
            # yt-dlp prints zero-padded dates; PyPI does not.
            year, month, day = version.split(".")
            executable = Path(command[0]).parent / "yt-dlp"
            executable.write_text(f"{year}.{int(month):02d}.{int(day):02d}")
        elif command[-1] == "--version":
            return subprocess.CompletedProcess(
                command, 0, Path(command[0]).read_text() + "\n", ""
            )
        return subprocess.CompletedProcess(command, 0, "", "")


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def _runtime(
    tmp_path: Path,
    installer: FakeInstaller,
    releases: list[str],
    clock: Clock,
) -> YtDlpRuntime:
    return YtDlpRuntime(
        tmp_path / "yt-dlp",
        fallback_executable="/app/.venv/bin/yt-dlp",
        python_executable="/app/.venv/bin/python",
        now=clock,
        run=installer,
        latest_release=lambda: releases[-1],
    )


def test_the_locked_yt_dlp_is_used_until_the_first_install(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, FakeInstaller(), ["2026.10.26"], Clock())

    assert runtime.executable == "/app/.venv/bin/yt-dlp"


def test_the_latest_release_is_installed_and_activated(tmp_path: Path) -> None:
    installer = FakeInstaller()
    runtime = _runtime(tmp_path, installer, ["2026.10.26"], Clock())

    assert runtime.refresh() is True

    assert runtime.executable == str(
        tmp_path / "yt-dlp" / "current" / "bin" / "yt-dlp"
    )
    assert (tmp_path / "yt-dlp" / "current").resolve() == (
        tmp_path / "yt-dlp" / "versions" / "2026.10.26"
    )
    pip = next(command for command in installer.commands if "pip" in command)
    assert pip[-1] == "yt-dlp[default,curl-cffi]==2026.10.26"


def test_releases_are_checked_at_most_once_a_day(tmp_path: Path) -> None:
    installer = FakeInstaller()
    clock = Clock()
    releases = ["2026.10.26"]
    runtime = _runtime(tmp_path, installer, releases, clock)
    runtime.refresh()
    releases.append("2026.11.2")

    clock.value += timedelta(hours=6)
    assert runtime.refresh() is False
    assert runtime.refresh(force=True) is True
    assert runtime.version() == "2026.11.02"


def test_the_same_release_written_differently_is_not_reinstalled(
    tmp_path: Path,
) -> None:
    installer = FakeInstaller()
    clock = Clock()
    runtime = _runtime(tmp_path, installer, ["2026.7.4"], clock)
    runtime.refresh()
    installs = len(installer.commands)

    clock.value += timedelta(days=2)
    assert runtime.refresh() is False
    assert len(installer.commands) == installs + 1  # only --version


def test_a_failed_install_keeps_the_current_version(tmp_path: Path) -> None:
    installer = FakeInstaller()
    clock = Clock()
    releases = ["2026.10.26"]
    runtime = _runtime(tmp_path, installer, releases, clock)
    runtime.refresh()
    releases.append("2026.11.2")
    installer.fail_pip = True

    assert runtime.refresh(force=True) is False

    assert runtime.version() == "2026.10.26"
    assert not (tmp_path / "yt-dlp" / "versions" / "2026.11.2").exists()


def test_an_unreachable_index_keeps_the_current_version(tmp_path: Path) -> None:
    def unreachable() -> str:
        raise httpx.ConnectError("offline")

    runtime = YtDlpRuntime(
        tmp_path / "yt-dlp",
        fallback_executable="/app/.venv/bin/yt-dlp",
        run=FakeInstaller(),
        latest_release=unreachable,
    )

    assert runtime.refresh() is False
    assert runtime.executable == "/app/.venv/bin/yt-dlp"


def test_only_the_active_and_previous_releases_are_kept(tmp_path: Path) -> None:
    installer = FakeInstaller()
    clock = Clock()
    releases = ["2026.10.12"]
    runtime = _runtime(tmp_path, installer, releases, clock)
    for release in ("2026.10.12", "2026.10.26", "2026.11.2"):
        releases.append(release)
        clock.value += timedelta(days=2)
        runtime.refresh()

    kept = sorted(path.name for path in (tmp_path / "yt-dlp" / "versions").iterdir())
    assert kept == ["2026.10.26", "2026.11.2"]
