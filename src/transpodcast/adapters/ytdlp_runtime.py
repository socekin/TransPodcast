"""A yt-dlp install that follows upstream releases.

Sites change how they serve media often, and yt-dlp ships fixes within
days. TransPodcast therefore runs yt-dlp from its own virtual environment under
``root`` and moves to each new release, instead of running the version
locked with the application. The locked yt-dlp stays the fallback until the
first install succeeds, and a failed upgrade keeps the current version.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

LOGGER = logging.getLogger(__name__)

_PYPI_RELEASE_URL = "https://pypi.org/pypi/yt-dlp/json"
_REQUIREMENT = "yt-dlp[default,curl-cffi]"


class YtDlpRuntime:
    def __init__(
        self,
        root: Path,
        fallback_executable: str,
        *,
        python_executable: str = sys.executable,
        proxy_url: str = "",
        check_interval: timedelta = timedelta(hours=24),
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        latest_release: Callable[[], str] | None = None,
    ) -> None:
        self.root = root.expanduser().absolute()
        self.fallback_executable = fallback_executable
        self.python_executable = python_executable
        self.proxy_url = proxy_url.strip()
        self.check_interval = check_interval
        self._now = now
        self._run = run
        self._latest_release = latest_release or self._pypi_latest_release

    @property
    def executable(self) -> str:
        current = self.root / "current" / "bin" / "yt-dlp"
        return str(current) if current.is_file() else self.fallback_executable

    def available(self) -> bool:
        return Path(self.executable).is_file() or (
            shutil.which(self.executable) is not None
        )

    def version(self) -> str | None:
        try:
            completed = self._run(
                [self.executable, "--version"],
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    def refresh(self, *, force: bool = False) -> bool:
        """Install the latest release if it is newer; True when it changed.

        Without ``force`` the release is checked at most once per
        ``check_interval``. Errors are logged and keep the current version.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock():
            if not force and self._checked_recently():
                return False
            self._mark_checked()
            try:
                latest = self._latest_release()
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
                LOGGER.warning("Could not look up the latest yt-dlp: %s", error)
                return False
            if _same_release(self._managed_version(), latest):
                return False
            target = self.root / "versions" / latest
            try:
                self._install(target, latest)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                LOGGER.warning("Could not install yt-dlp %s: %s", latest, error)
                shutil.rmtree(target, ignore_errors=True)
                return False
            self._activate(target)
            self._prune(keep=target)
            LOGGER.info("yt-dlp %s is now active", latest)
            return True

    def _managed_version(self) -> str | None:
        current = self.root / "current"
        if not (current / "bin" / "yt-dlp").is_file():
            return None
        return self.version()

    def _install(self, target: Path, version: str) -> None:
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._checked(
            [self.python_executable, "-m", "venv", str(target)],
            timeout=300,
        )
        command = [
            str(target / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--quiet",
        ]
        if self.proxy_url:
            command.extend(("--proxy", self.proxy_url))
        command.append(f"{_REQUIREMENT}=={version}")
        self._checked(command, timeout=900)
        installed = self._checked(
            [str(target / "bin" / "yt-dlp"), "--version"],
            timeout=60,
        ).stdout.strip()
        if not _same_release(installed, version):
            raise RuntimeError(
                f"installed yt-dlp reports {installed!r}, expected {version!r}"
            )

    def _checked(
        self,
        command: list[str],
        *,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        completed = self._run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"{Path(command[0]).name} failed: "
                + completed.stderr.strip()[-500:]
            )
        return completed

    def _activate(self, target: Path) -> None:
        # Swap the link atomically so a running download keeps its files.
        link = self.root / "current"
        staging = self.root / "current.staging"
        staging.unlink(missing_ok=True)
        staging.symlink_to(
            target.relative_to(self.root),
            target_is_directory=True,
        )
        os.replace(staging, link)

    def _prune(self, *, keep: Path) -> None:
        versions = sorted(
            (path for path in (self.root / "versions").iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        # Keep the active release and the one before it.
        for path in [item for item in versions if item != keep][1:]:
            shutil.rmtree(path, ignore_errors=True)

    def _checked_recently(self) -> bool:
        marker = self.root / "last-check"
        if not marker.is_file():
            return False
        checked_at = datetime.fromtimestamp(marker.stat().st_mtime, tz=UTC)
        return self._now() - checked_at < self.check_interval

    def _mark_checked(self) -> None:
        marker = self.root / "last-check"
        marker.touch()
        stamp = self._now().timestamp()
        os.utime(marker, (stamp, stamp))

    @contextmanager
    def _lock(self) -> Iterator[None]:
        # The API and the worker share one install.
        with open(self.root / ".lock", "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _pypi_latest_release(self) -> str:
        response = (
            httpx.get(_PYPI_RELEASE_URL, timeout=30, proxy=self.proxy_url)
            if self.proxy_url
            else httpx.get(_PYPI_RELEASE_URL, timeout=30)
        )
        response.raise_for_status()
        return str(response.json()["info"]["version"])


def _same_release(left: str | None, right: str | None) -> bool:
    """PyPI writes 2026.7.4 where ``yt-dlp --version`` prints 2026.07.04."""
    if not left or not right:
        return False
    return [int(part) for part in re.findall(r"\d+", left)] == [
        int(part) for part in re.findall(r"\d+", right)
    ]
