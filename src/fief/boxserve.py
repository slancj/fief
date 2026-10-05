"""Box onboarding artifacts: what `curl $HUB/add.sh | sh` downloads.

Public code + public binaries only — never secrets. Three artifacts per
box arch, all cacheable on the hub's (ephemeral) disk:

- ``fief.tgz`` — this package's source, tarred from disk on demand.
- ``chisel-<arch>`` — pinned chisel binary, fetched once from upstream.
- ``mesh-<arch>.tgz`` — pinned mesh tarball, fetched once from upstream.

Upstream URLs are pinned constants (never user-controlled), so these
endpoints are not an open proxy. Fetches are integrity-checked against
upstream checksums before caching; ``SHA256SUMS`` lets the box re-verify.

Scrub note (see tests/test_hf_payload_clean.py): this module ships to
HF, so mesh vendor literals stay base64-encoded like in mesh_fetch.py —
no plaintext vendor signatures in shipped files.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import re
import tarfile
import time
from collections.abc import Callable
from pathlib import Path

from . import __version__
from .config import DEFAULT_CHISEL_VERSION, DEFAULT_MESH_VERSION
from .fetch import fetch, verify_sha256
from .store import cache_dir

#: Box arches end to end: add.sh sends one of these, vendors map from it.
BOX_ARCHES = ("amd64", "arm64")

#: chisel arch names coincide with box arches (armv7 boxes come later).
_CHISEL_ARCH = {"amd64": "amd64", "arm64": "arm64"}
#: Mesh arch names differ (mirrors mesh_fetch.MACHINE_TO_ARCH values).
_MESH_ARCH = {"amd64": "amd64", "arm64": "arm"}


def _d(s: str) -> str:
    return base64.b64decode(s.encode()).decode()


_CHISEL_UPSTREAM = "https://github.com/jpillora/chisel/releases/download"
_MESH_UPSTREAM = _d("aHR0cHM6Ly9wa2dzLnRhaWxzY2FsZS5jb20vc3RhYmxl")
_MESH_PRODUCT = _d("dGFpbHNjYWxl")

_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.\-]*$")


def box_dir() -> Path:
    return cache_dir("FIEF_BOX_DIR", "box")


def check_arch(arch: str) -> str:
    if arch not in BOX_ARCHES:
        raise KeyError(f"unsupported box arch: {arch!r} (want one of {BOX_ARCHES})")
    return arch


def check_version(version: str) -> str:
    if not _VERSION_RE.match(version):
        raise ValueError(f"bad version: {version!r}")
    return version


def chisel_asset(version: str, arch: str) -> tuple[str, str]:
    """(download URL, cache filename) for the pinned chisel binary."""
    version = check_version(version)
    vend = _CHISEL_ARCH[check_arch(arch)]
    name = f"chisel_{version}_linux_{vend}.gz"
    return f"{_CHISEL_UPSTREAM}/v{version}/{name}", name


def mesh_asset(version: str, arch: str) -> tuple[str, str]:
    """(download URL, cache filename) for the pinned mesh tarball."""
    version = check_version(version)
    vend = _MESH_ARCH[check_arch(arch)]
    name = f"{_MESH_PRODUCT}_{version}_{vend}.tgz"
    return f"{_MESH_UPSTREAM}/{name}", name


def _acquire(directory: Path, log: Callable[[str], None] | None = None) -> None:
    """Cross-process single-flight: mkdir is atomic; stale locks (>15 min)
    are stolen so a crashed fetcher can't wedge enrollments forever."""
    emit = log or (lambda msg: None)
    lock = directory / ".lock"
    deadline = time.time() + 600
    lock.parent.mkdir(parents=True, exist_ok=True)
    while time.time() < deadline:
        try:
            lock.mkdir()
            return
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0
            if age > 900:
                emit("stealing stale artifact lock")
                try:
                    lock.rmdir()
                except OSError:
                    pass
            time.sleep(2)
    raise RuntimeError("timed out waiting for artifact fetch")


def _release(directory: Path) -> None:
    try:
        (directory / ".lock").rmdir()
    except OSError:
        pass


def _parse_checksum(sums_text: str, name: str) -> str | None:
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == name:
            return parts[0]
    return None


def get_chisel(
    version: str = DEFAULT_CHISEL_VERSION,
    arch: str = "amd64",
    log: Callable[[str], None] | None = None,
) -> Path:
    """Fetch-once the pinned chisel binary (decompressed). Return its path."""
    emit = log or (lambda msg: None)
    url, gz_name = chisel_asset(version, arch)
    directory = box_dir()
    target = directory / f"chisel-{version}-{arch}"
    if target.exists():
        return target
    _acquire(directory, emit)
    try:
        if target.exists():
            return target
        emit(f"fetching chisel {version}/{arch} for a box ...")
        gz_data = fetch(url)
        sums = fetch(f"{url.rsplit('/', 1)[0]}/chisel_{version}_checksums.txt")
        want = _parse_checksum(sums.decode(), gz_name)
        if not want:
            raise RuntimeError("checksum entry not found for " + gz_name)
        verify_sha256(gz_data, want, gz_name)
        target.write_bytes(gzip.decompress(gz_data))
        target.chmod(0o755)
        return target
    finally:
        _release(directory)


def get_mesh_tgz(
    version: str = DEFAULT_MESH_VERSION,
    arch: str = "amd64",
    log: Callable[[str], None] | None = None,
) -> Path:
    """Fetch-once the pinned mesh tarball (as upstream ships it)."""
    emit = log or (lambda msg: None)
    url, tgz_name = mesh_asset(version, arch)
    directory = box_dir()
    target = directory / f"mesh-{version}-{arch}.tgz"
    if target.exists():
        return target
    _acquire(directory, emit)
    try:
        if target.exists():
            return target
        emit(f"fetching mesh {version}/{arch} for a box ...")
        data = fetch(url)
        sums = fetch(f"{url}.sha256").decode()
        verify_sha256(data, sums, tgz_name)
        target.write_bytes(data)
        return target
    finally:
        _release(directory)


def source_tgz() -> tuple[str, bytes]:
    """Tar the running package (no caches). Small; generated per request."""
    root = Path(__file__).resolve().parent
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tf.add(path, arcname=f"fief/{path.name}")
    return f"fief-{__version__}.tgz", buf.getvalue()


def versions(
    chisel_version: str = DEFAULT_CHISEL_VERSION,
    mesh_version: str = DEFAULT_MESH_VERSION,
) -> bytes:
    """Pinned versions add.sh records into the mesh .version marker."""
    return (
        f"CHISEL_VERSION={check_version(chisel_version)}\n"
        f"MESH_VERSION={check_version(mesh_version)}\n"
        f"FIEF_VERSION={__version__}\n"
    ).encode()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256sums(
    chisel_version: str = DEFAULT_CHISEL_VERSION,
    mesh_version: str = DEFAULT_MESH_VERSION,
    log: Callable[[str], None] | None = None,
) -> bytes:
    """Checksum manifest over every artifact add.sh downloads.

    Ensuring each entry fetches-and-caches on first hit, so downloads
    right after are cache hits.
    """
    lines = []
    name, data = source_tgz()
    lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    for arch in BOX_ARCHES:
        target = get_chisel(chisel_version, arch, log)
        lines.append(f"{_sha256(target)}  chisel-{arch}")
        target = get_mesh_tgz(mesh_version, arch, log)
        lines.append(f"{_sha256(target)}  mesh-{arch}.tgz")
    return ("\n".join(lines) + "\n").encode()


#: Flattened layout add.sh installs, mirroring ensure_chisel/ensure_mesh
#: so a fresh box never re-downloads: $FIEF_BIN_DIR/{chisel,mesh-bin/…}.
ADD_SH = r"""#!/bin/sh
# fief box installer: `curl $HUB/add.sh | sh`. Needs sh + curl + base64 +
# python3 (>=3.10). Public artifacts only come from the hub; the one secret
# paste (`fief config invite` blob) is never logged or transmitted back.
set -eu
HUB="${1:-__HUB__}"
DEST="${HOME}/.local/fief-box"
ARCH=""
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1" >&2; exit 1; }; }
need curl; need base64; need python3
case "$(uname -m)" in
  x86_64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) echo "unsupported CPU: $(uname -m)" >&2; exit 1 ;;
esac
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  if command -v apt-get >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo apt-get update && sudo apt-get install -y --no-install-recommends python3
  fi
  python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    || { echo "python3 >= 3.10 required" >&2; exit 1; }
fi
mkdir -p "$DEST/bin" "$DEST/src"
cd "$DEST"
curl -fsSL "$HUB/box/versions" -o versions
curl -fsSL "$HUB/box/SHA256SUMS" -o SHA256SUMS
curl -fsSL "$HUB/box/fief.tgz" -o fief.tgz
curl -fsSL "$HUB/box/chisel-$ARCH" -o chisel.bin
curl -fsSL "$HUB/box/mesh-$ARCH.tgz" -o mesh.tgz
MESH_VERSION="$(grep '^MESH_VERSION=' versions | cut -d= -f2)"
BRAND="$(printf 'dGFpbHNjYWxl' | base64 -d)"
python3 - "$ARCH" "$MESH_VERSION" "$BRAND" <<'PYEOF'
import hashlib, os, sys, tarfile
arch, mesh_version, brand = sys.argv[1], sys.argv[2], sys.argv[3]
sums = {}
for line in open("SHA256SUMS"):
    h, name = line.split()
    sums[name.strip()] = h
def check(path, name):
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if h != sums[name]:
        raise SystemExit(f"checksum mismatch for {name}")
    print(f"verified {name}")
check("fief.tgz", [k for k in sums if k.startswith("fief-")][0])
check("chisel.bin", f"chisel-{arch}")
check("mesh.tgz", f"mesh-{arch}.tgz")
with tarfile.open("fief.tgz") as tf:
    tf.extractall("src")
open("bin/chisel", "wb").write(open("chisel.bin", "rb").read())
os.chmod("bin/chisel", 0o755)
os.makedirs("bin/mesh-bin", exist_ok=True)
with tarfile.open("mesh.tgz") as tf:
    for member in tf.getmembers():
        base = member.name.rsplit("/", 1)[-1]
        if base == brand:
            dest = "bin/mesh-bin/fiefmesh"
        elif base == brand + "d":
            dest = "bin/mesh-bin/fiefmeshd"
        else:
            continue
        if member.isfile():
            open(dest, "wb").write(tf.extractfile(member).read())
            os.chmod(dest, 0o755)
open("bin/mesh-bin/.version", "w").write(mesh_version + "\n")
print("binaries staged (chisel + mesh, version-pinned)")
PYEOF
printf 'paste the invite blob from `fief config invite` on your laptop:\n> '
read -r BLOB </dev/tty
printf '%s' "$BLOB" | base64 -d > box.env
for k in HUB_URL CHISEL_AUTH FIEF_MESH_KEY FIEF_MESH_HOSTNAME; do
  grep -q "^$k=" box.env || { echo "blob missing $k" >&2; exit 1; }
done
chmod 600 box.env
cat > box-run.sh <<'RUNEOF'
#!/bin/sh
# Supervised box flow: exit tunnel, then mesh through its egress proxy.
cd "$(dirname "$0")"
set -a; . ./box.env; set +a
export PYTHONPATH="$PWD/src"
export FIEF_BIN_DIR="$PWD/bin"
supervise() { while :; do "$@" || true; sleep 10; done; }
supervise python3 -m fief exit &
wait_egress() {
  python3 -c 'import socket,sys,time; d=time.time()+180
while time.time()<d:
 try:
  socket.create_connection(("127.0.0.1",1081),timeout=2).close(); raise SystemExit(0)
 except OSError: time.sleep(2)
raise SystemExit(1)' || { echo "egress 1081 never opened" >&2; exit 1; }
}
wait_egress
supervise env FIEF_MESH_PROXY="socks5h://127.0.0.1:1081" python3 -m fief mesh up &
wait
RUNEOF
chmod +x box-run.sh
# Persistence, best effort (no root required); report the tier reached.
UNIT_HOME="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
if command -v systemctl >/dev/null 2>&1; then
  mkdir -p "$UNIT_HOME"
  printf '[Unit]\nDescription=fief box (tunnel + mesh)\nAfter=network-online.target\n[Service]\nExecStart=%s/box-run.sh\nRestart=always\nRestartSec=10\n[Install]\nWantedBy=default.target\n' "$DEST" > "$UNIT_HOME/fief-box.service"
  systemctl --user daemon-reload 2>/dev/null || true
fi
if command -v systemctl >/dev/null 2>&1 && systemctl --user enable --now fief-box 2>/dev/null; then
  echo "persistence: systemd user unit"
elif command -v crontab >/dev/null 2>&1 && { crontab -l 2>/dev/null; echo "@reboot $DEST/box-run.sh"; } | crontab - 2>/dev/null; then
  echo "persistence: cron @reboot"
else
  echo "persistence: NONE — re-run $DEST/box-run.sh after reboot"
fi
echo "starting tunnel + mesh in the background ..."
nohup "$DEST/box-run.sh" >/dev/null 2>&1 &
NAME="$(grep '^FIEF_MESH_HOSTNAME=' box.env | cut -d= -f2)"
TOOL="$(printf 'dGFpbHNjYWxl' | base64 -d)"
echo "done. On your laptop: $TOOL ssh $NAME"
"""


def add_sh(hub_url: str) -> str:
    """Installer script with the download origin baked in."""
    hub = (hub_url or "").strip().rstrip("/")
    if not hub.startswith("https://"):
        raise ValueError("hub URL must be https")
    return ADD_SH.replace("__HUB__", hub)


def _valid_host(host: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,253}$", host or ""))


def public_url(host: str, fallback: str) -> str:
    """Same-origin download base: the Host the box already dials."""
    if _valid_host(host):
        return f"https://{host}"
    return fallback.rstrip("/")


def route(
    path: str,
    host: str = "",
    hub_url: str = "",
    log: Callable[[str], None] | None = None,
) -> tuple[int, bytes, str] | None:
    """Serve box artifacts. None = not a box path (caller falls through).

    Returns (status, body, content-type). Never serves secrets: only the
    static installer, the package source, and pinned public binaries.
    """
    emit = log or (lambda msg: None)
    if path == "/add.sh":
        try:
            body = add_sh(public_url(host, hub_url or "https://localhost")).encode()
        except ValueError:
            return 404, b"Not found\n", "text/plain"
        return 200, body, "text/x-shellscript"
    if path == "/box/versions":
        return 200, versions(), "text/plain"
    if path == "/box/SHA256SUMS":
        try:
            return 200, sha256sums(log=emit), "text/plain"
        except (OSError, RuntimeError) as exc:
            emit(f"box sums failed: {exc}")
            return 503, b"artifact fetch failed, retry shortly\n", "text/plain"
    if path == "/box/fief.tgz":
        _, data = source_tgz()
        return 200, data, "application/gzip"
    m = re.fullmatch(r"/box/chisel-(amd64|arm64)", path or "")
    if m:
        try:
            data = get_chisel(arch=m.group(1), log=emit).read_bytes()
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            emit(f"box chisel fetch failed: {exc}")
            return 503, b"artifact fetch failed, retry shortly\n", "text/plain"
        return 200, data, "application/octet-stream"
    m = re.fullmatch(r"/box/mesh-(amd64|arm64)\.tgz", path or "")
    if m:
        try:
            data = get_mesh_tgz(arch=m.group(1), log=emit).read_bytes()
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            emit(f"box mesh fetch failed: {exc}")
            return 503, b"artifact fetch failed, retry shortly\n", "text/plain"
        return 200, data, "application/gzip"
    return None
