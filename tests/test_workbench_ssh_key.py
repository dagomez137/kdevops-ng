# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the VM ssh-config header builder (`f.workbench.ssh_key`)."""

from pathlib import Path

from f.workbench.ssh_key import _config_header


def test_config_header_puts_the_include_at_global_scope():
    text = _config_header(Path("/home/op/.local/state/system/ssh"))
    body = [ln for ln in text.splitlines() if not ln.startswith("#")]
    include = "Include /home/op/.local/state/system/ssh/config.d/*.conf"
    assert include in body
    assert body.index(include) < body.index("Host vsock/*")


def test_config_header_pins_the_managed_identity_under_the_vsock_host():
    text = _config_header(Path("/srv/ssh"))
    host = text.split("Host vsock/*", 1)[1]
    assert "    IdentityFile /srv/ssh/id_ed25519" in host
    assert "    IdentitiesOnly yes" in host
    assert "    UserKnownHostsFile /dev/null" in host


def test_config_header_documents_the_one_operator_include_line():
    text = _config_header(Path("/srv/ssh"))
    assert "#     Include /srv/ssh/config" in text
    assert text.endswith("\n")
