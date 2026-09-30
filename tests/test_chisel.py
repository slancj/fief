import pytest

from fief.chisel import asset_name, parse_checksum, target_triple


@pytest.mark.parametrize(
    ("machine", "arch"),
    [
        ("x86_64", "amd64"),
        ("i386", "386"),
        ("i686", "386"),
        ("aarch64", "arm64"),
        ("armv7l", "armv7"),
        ("armv6l", "armv6"),
    ],
)
def test_target_triple_linux(machine, arch):
    assert target_triple("linux", machine) == ("linux", arch)


def test_target_triple_darwin():
    assert target_triple("darwin", "aarch64") == ("darwin", "arm64")


def test_target_triple_unknown():
    with pytest.raises(RuntimeError, match="unsupported CPU"):
        target_triple("linux", "riscv64")
    with pytest.raises(RuntimeError, match="unsupported OS"):
        target_triple("windows", "x86_64")


def test_asset_name():
    assert asset_name("1.12.0", "linux", "amd64") == "chisel_1.12.0_linux_amd64.gz"
    assert asset_name("1.12.0", "linux", "arm64") == "chisel_1.12.0_linux_arm64.gz"


def test_parse_checksum_exact_match():
    sums = (
        "e0e84b4eefcc99b82836794eb4aa6ec2915445ee1655c84d20711966d54a97a0"
        "  chisel_1.12.0_linux_amd64.deb\n"
        "f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6"
        "  chisel_1.12.0_linux_amd64.gz\n"
    )
    assert (
        parse_checksum(sums, "chisel_1.12.0_linux_amd64.gz")
        == "f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6"
    )


def test_parse_checksum_missing():
    assert (
        parse_checksum("abc  some_other_file.gz\n", "chisel_1.12.0_linux_amd64.gz")
        is None
    )
