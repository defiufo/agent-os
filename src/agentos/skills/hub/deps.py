"""Dependency installation for skills — brew, npm, go, uv, download.

Which kinds exist and what each one runs lives in
:mod:`agentos.skills.install_kinds`, shared with the agent tool and with
the display-only hints so the three can't drift apart again.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import structlog

from agentos.skills.install_kinds import (
    ARGV_INSTALL_KINDS,
    BIN_NAME_RE,
    DOWNLOAD_URL_RE,
    MANUAL_INSTALL_KINDS,
    InstallSpecError,
    build_install_argv,
    is_supported_install_kind,
    normalize_install_kind,
    render_install_command,
)
from agentos.skills.types import SkillInstallSpec

log = structlog.get_logger(__name__)


@dataclass
class DepResult:
    """Result of installing a single dependency."""

    kind: str
    identifier: str
    success: bool
    message: str = ""


async def _run(cmd: list[str], timeout: float = 120.0) -> tuple[int, str, str]:
    """Run a subprocess with timeout."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return -1, "", "Timed out"
    return proc.returncode or 0, stdout.decode(), stderr.decode()


async def _install_via_argv(spec: SkillInstallSpec) -> DepResult:
    """Run the shared command for an argv-shaped install kind."""
    kind = normalize_install_kind(spec.kind)
    identifier = spec.formula or spec.package or spec.module or spec.id
    try:
        argv = build_install_argv(spec)
    except InstallSpecError as exc:
        return DepResult(kind=kind, identifier=identifier, success=False, message=str(exc))

    code, out, err = await _run(argv)
    if code == 0:
        return DepResult(kind=kind, identifier=identifier, success=True, message="Installed")
    return DepResult(kind=kind, identifier=identifier, success=False, message=err.strip()[:200])


#: Redirect hops allowed on a download, matching the web_fetch fetch loop.
_MAX_DOWNLOAD_REDIRECTS = 5
#: Ceiling on a downloaded artifact. Generous for a real binary, bounded enough
#: that a hostile URL cannot fill the disk.
_MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024


def _resolve_download_dest(spec: SkillInstallSpec, url: str) -> Path:
    """Return the path a download may write, or raise :class:`InstallSpecError`.

    Everything reaching this function comes from skill frontmatter, and a hub
    skill is third-party content. ``bins[0]`` used to flow straight into a path
    and a chmod, so a name like ``../../../.profile`` wrote outside the binary
    directory. The rules below are the ones
    :func:`~agentos.skills.install_kinds.render_install_command` already applied
    to the *displayed* command; applying them here is what stops the executor
    and the hint from disagreeing.
    """
    raw_name = spec.bins[0] if spec.bins else url.rsplit("/", 1)[-1]
    if not BIN_NAME_RE.match(raw_name or ""):
        raise InstallSpecError(f"Unsafe download target name: {raw_name!r}")

    bin_dir = Path.home() / ".local" / "bin"
    dest = bin_dir / raw_name

    # A pre-existing symlink here would be written *through*, clobbering
    # whatever it points at. Refuse rather than follow it.
    if dest.is_symlink():
        raise InstallSpecError(f"Download target is a symlink: {raw_name!r}")

    # resolve() collapses any traversal the name regex somehow admits and
    # follows a symlinked bin_dir to where it really is, so compare after it.
    resolved = dest.resolve()
    root = bin_dir.resolve()
    if resolved == root or resolved.parent != root:
        raise InstallSpecError(f"Download target escapes {bin_dir}: {raw_name!r}")
    return dest


async def _fetch_to_file(url: str, dest: Path) -> None:
    """Fetch ``url`` into ``dest``, validating the URL and every redirect hop.

    The previous implementation shelled out to ``curl -fsSL``. ``-L`` follows
    redirects, so validating only the URL the skill declared meant a hop to a
    private or metadata address was never checked. Redirects are followed here
    one at a time with the same guard applied to each, mirroring
    :mod:`agentos.tools.builtin.web_fetch`.
    """
    from agentos.tools.ssrf import validate_http_url_for_fetch
    from agentos.tools.ssrf_client import ssrf_guarded_client

    current_url = url
    async with ssrf_guarded_client(timeout=120.0, follow_redirects=False) as client:
        for _hop in range(_MAX_DOWNLOAD_REDIRECTS + 1):
            validate_http_url_for_fetch(current_url)
            request = client.build_request("GET", current_url)
            response = await client.send(request, stream=True)
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect without a Location header")
                    current_url = urljoin(str(response.url), location)
                    continue
                response.raise_for_status()
                await _stream_to_disk(response, dest)
                return
            finally:
                await response.aclose()
        raise ValueError(f"Too many redirects (>{_MAX_DOWNLOAD_REDIRECTS})")


async def _stream_to_disk(response: Any, dest: Path) -> None:
    """Write a streamed response to ``dest`` atomically, under a size ceiling.

    The bytes land in a temporary file that is made executable and renamed into
    place only once the whole body has arrived, so a truncated or oversized
    download never leaves an executable behind.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".part")
    tmp_path = Path(tmp_name)
    written = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            async for chunk in response.aiter_bytes():
                written += len(chunk)
                if written > _MAX_DOWNLOAD_BYTES:
                    raise ValueError(f"Download exceeds {_MAX_DOWNLOAD_BYTES} byte cap")
                handle.write(chunk)
        tmp_path.chmod(0o755)
        os.replace(tmp_path, dest)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


async def install_download(spec: SkillInstallSpec) -> DepResult:
    """Download a binary from a URL into ``~/.local/bin``."""
    import shutil

    url = spec.url
    if not url or not DOWNLOAD_URL_RE.match(url):
        return DepResult(
            kind="download", identifier=url or "", success=False, message=f"Invalid URL: {url}"
        )

    try:
        dest = _resolve_download_dest(spec, url)
    except InstallSpecError as exc:
        return DepResult(kind="download", identifier=url, success=False, message=str(exc))

    bin_name = dest.name
    try:
        await _fetch_to_file(url, dest)
    except Exception as exc:
        return DepResult(kind="download", identifier=url, success=False, message=str(exc)[:200])

    # Verify it landed on PATH
    if shutil.which(bin_name):
        return DepResult(
            kind="download", identifier=bin_name, success=True, message=f"Downloaded to {dest}"
        )
    return DepResult(
        kind="download",
        identifier=bin_name,
        success=True,
        message=f"Downloaded to {dest} (may need PATH update)",
    )


# Keyed by canonical kind — see agentos.skills.install_kinds.
_INSTALLERS = {
    **{kind: _install_via_argv for kind in ARGV_INSTALL_KINDS},
    "download": install_download,
}


async def install_deps(specs: list[SkillInstallSpec]) -> list[DepResult]:
    """Install all dependencies for a skill. Returns results per spec."""
    results = []
    for spec in specs:
        kind = normalize_install_kind(spec.kind)
        handler = _INSTALLERS.get(kind)
        if handler is None:
            if kind in MANUAL_INSTALL_KINDS:
                # Spell the command out: nothing else on this path shows it,
                # and telling the operator to go find it is a dead end.
                command = render_install_command(spec)
                message = f"Install kind '{kind}' needs elevated privileges"
                message += f" — run: {command}" if command else " and cannot be run here"
            elif is_supported_install_kind(kind):  # pragma: no cover - defensive
                message = f"No installer wired for kind: {kind}"
            else:
                message = f"Unsupported install kind: {spec.kind}"
            results.append(DepResult(kind=kind, identifier=spec.id, success=False, message=message))
            continue
        try:
            result = await handler(spec)
        except FileNotFoundError:
            result = DepResult(
                kind=kind,
                identifier=spec.id,
                success=False,
                message=f"Tool not found for kind '{kind}' (brew/npm/go/uv)",
            )
        except Exception as exc:
            result = DepResult(
                kind=kind,
                identifier=spec.id,
                success=False,
                message=f"Error: {exc}",
            )
        results.append(result)
        log.info("deps.install", kind=kind, id=spec.id, success=result.success)
    return results
