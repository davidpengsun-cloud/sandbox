import subprocess

from tests.terraform_runs import handshake_failed


def done(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["terraform"], code, out, err)


def test_only_a_failed_plugin_handshake_is_retried():
    assert handshake_failed(done(1, err="Error: Failed to negotiate the initial go-plugin protocol handshake"))
    assert handshake_failed(done(1, err="Unrecognized remote plugin message: x"))
    assert not handshake_failed(done(0, err="Failed to negotiate"))
    assert not handshake_failed(done(1, err="Error: Unsupported argument"))
    assert not handshake_failed(done(1, out="1 passed, 1 failed"))
