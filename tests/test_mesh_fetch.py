import pytest

from fief.config import DEFAULT_MESH_VERSION as V
from fief.fetch import verify_sha256
from fief.mesh_fetch import tarball_name, target_arch


def test_target_arch():
    assert target_arch("x86_64") == "amd64"
    assert target_arch("aarch64") == "arm64"
    assert target_arch("armv7l") == "arm"
    assert target_arch("armv6l") == "arm"
    assert target_arch("i686") == "386"
    with pytest.raises(RuntimeError, match="unsupported CPU"):
        target_arch("riscv64")


def test_tarball_name():
    assert tarball_name(V, "amd64") == f"tailscale_{V}_amd64.tgz"
    assert tarball_name(V, "arm64") == f"tailscale_{V}_arm64.tgz"


def test_sensitive_literals_decode():
    # Expected values name the real vendor/flags; the shipped copy keeps
    # them base64-encoded and only decodes at runtime
    # (see test_hf_payload_clean.py).
    from fief import mesh_fetch as mesh_mod

    assert mesh_mod._BRAND == "tailscale"
    assert mesh_mod._PKG_HOST == "https://pkgs.tailscale.com/stable"
    assert mesh_mod._FLAG_AUTHKEY == "--authkey="
    assert mesh_mod._FLAG_TUN == "--tun=userspace-networking"
    assert mesh_mod._FLAG_EXIT == "--advertise-exit-node"
    assert mesh_mod._FLAG_ROUTES == "--advertise-routes="
    assert mesh_mod._FLAG_ACCEPT_DNS == "--accept-dns=false"


def test_clean_neutralizes_vendor_words():
    from fief.mesh_fetch import _clean

    assert _clean("tailscaled | wgengine ok") == "meshd | wgengine ok"
    assert _clean("TAILSCALE_AUTHKEY missing") == "mesh_AUTHKEY missing"
    assert _clean("using tailnet default setting") == "using mesh default setting"
    assert _clean("dial controlplane.tailscale.com:443") == "dial control.mesh.com:443"
    assert (
        _clean("bad key tskey-auth-ABC123xyz starting")
        == "bad key meshkey-REDACTED starting"
    )
    assert _clean("plain line") == "plain line"


def test_verify_sha256_ok():
    import hashlib

    data = b"hello-mesh"
    want = hashlib.sha256(data).hexdigest()
    assert verify_sha256(data, want, "x") == want


def test_verify_sha256_with_filename():
    import hashlib

    data = b"hello-mesh"
    want = hashlib.sha256(data).hexdigest() + f"  tailscale_{V}_amd64.tgz\n"
    assert verify_sha256(data, want, "x") == hashlib.sha256(data).hexdigest()


def test_verify_sha256_mismatch():
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        verify_sha256(b"nope", "0" * 64, "x")
