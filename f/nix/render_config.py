# SPDX-License-Identifier: copyleft-next-0.3.1
"""Render a per-VM imageless NixOS configuration: flake.nix + default.nix.

This is the Windmill equivalent of kdevops's `nixosfi` generate-configs phase. The
flake.nix is a near-verbatim copy of the vendored imageless *template* (only the
`nixos-flake` path input is set, plus one `<pkg>-src` input per source override);
default.nix is generated from the typed inputs and carries the per-VM composition
(which profiles/testSuites/mounts to import, hostname, SSH keys, source overrides).
The flake's own modules list already imports the imageless backend, the user module,
and the default overlay, so default.nix only adds to that.

Both files are written under `$WORKERS_DIR/$WORKER_INDEX/nix/<vm_name>/` — a
host-visible path, so a host-forked QEMU (qsu) can later serve the built closure.

Equivalent bash: scaffold from the imageless template, then edit the two files.

    nix flake init --template "path:$VENDOR_DIR/nixos-flake#imageless"
    # flake.nix:    set inputs.nixos-flake.url = "path:$VENDOR_DIR/nixos-flake"
    # default.nix:  imports = [ nixos-flake.nixosModules.profiles.devel ... ];
    #               networking.hostName = "<vm_name>"; users...authorizedKeys = [ ... ];
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from f.common.devshell import system_dir, vendor_dir

# Composable nixos-flake module attributes (see vendor/nixos-flake/flake.nix).
_PROFILES = {"build-tools", "controller", "devel", "monitoring"}
_TEST_SUITES = ["blktests", "fstests", "gitr", "ltp", "mmtests", "pynfs", "selftests", "sysbench"]

# Packages whose nixos-flake recipe a src override composes with, build-verified
# from a git checkout, scoped to the fstests focus: fio, xfstests and xfsprogs
# (overlays) and libbpf-tools (custom pkg, src from iovisor/bcc). Packages for other
# suites (spdk, xnvme, nfstest, pynfs, ...) join as verified. The advanced
# `extra_overrides` takes any other nixpkgs package.
_OVERRIDABLE_PKGS = ["fio", "xfstests", "xfsprogs", "libbpf-tools"]

# Profiles whose effect is behind an enable gate: importing alone is inert, so we
# turn them on when selected. devel and build-tools are active on import. controller
# is a host role (it pulls in libvirtd), so it is excluded from the featured default.
_PROFILE_ENABLE = {
    "monitoring": "nixos-flake.monitoring.enable",
    "controller": "nixos-flake.controller.enable",
}

# A fully-featured guest by default: every guest profile plus all test suites. Pare
# these back per run for a lighter closure.
_FEATURED_PROFILES = ["devel", "build-tools", "monitoring"]
_FEATURED_TEST_SUITES = list(_TEST_SUITES)

_VM_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
_PKG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")

# The two literals we surgically rewrite in the template. If the upstream template
# changes these, the replacements assert rather than silently no-op.
_TEMPLATE_PATH_PLACEHOLDER = "path:/path/to/nixos-flake"
_FOLLOWS_ANCHOR = '    nixpkgs.follows = "nixos-flake/nixpkgs";'


def main(
    vm_name: str = "nixos",
    profiles: list[str] | None = None,
    test_suites: list[str] | None = None,
    shares: dict | None = None,
    overrides: list[dict] | None = None,
    extra_overrides: list[dict] | None = None,
    ssh_keys: list[str] | None = None,
    user_name: str = "kdevops",
    home: bool = False,
    home_dir: str = "",
) -> dict:
    # A None/empty vm_name (e.g. an unset group passed through f/qsu/bringup, where flow
    # defaults are not materialized) falls back to the schema default instead of crashing.
    vm_name = vm_name or "nixos"
    # Drop empty rows the Windmill form adds for array/object fields (a blank
    # string, an empty {} override) so an untouched optional field is a no-op.
    profiles = [p for p in (profiles if profiles is not None else _FEATURED_PROFILES) if p]
    test_suites = [t for t in (test_suites if test_suites is not None else _FEATURED_TEST_SUITES) if t]
    # Curated overrides name a package from the form's dropdown (_OVERRIDABLE_PKGS);
    # extra_overrides takes any other nixpkgs package. Both are validated below.
    overrides = [ov for ov in (overrides or []) if ov]
    _reject_unknown("override package", [ov.get("pkg", "") for ov in overrides],
                    set(_OVERRIDABLE_PKGS))
    overrides = overrides + [ov for ov in (extra_overrides or []) if ov]
    ssh_keys = [k for k in (ssh_keys or []) if k and k.strip()]
    shares = {m: s for m, s in (shares or {}).items() if m and isinstance(s, dict) and s.get("tag")}
    # Predefined shares the operator should not have to declare by hand (they coexist
    # with the free-form `shares` above; an explicit entry for the same mount wins).
    # The matching host-served share is composed by f/qsu (qsu/common._shares).
    #  - fstests: auto whenever the closure runs the fstests suite.
    if "fstests" in test_suites:
        shares.setdefault("/var/lib/xfstests", {"tag": "fstests"})
    #  - home: the operator's host home (tag `home`, served once by qsu) mounted at
    #    /home/<operator> AND set as root's home (below), so `ssh <vm>` lands you straight
    #    in your home — writable via the root->operator virtiofsd uid-map, with no extra
    #    guest user and no sandbox change. A flow transform can't read the filesystem, so
    #    the path is resolved here; bringup passes /home/<host_user> from discover.
    home_dir = (home_dir or "").strip()
    if home and not home_dir:
        h = os.environ.get("HOME", "")
        home_dir = h if h.startswith("/home/") else "/home/kdevops"
    if home:
        shares.setdefault(home_dir, {"tag": "home"})

    if not _VM_NAME_RE.match(vm_name):
        raise ValueError(f"invalid vm_name {vm_name!r}: must match {_VM_NAME_RE.pattern}")
    _reject_unknown("profile", profiles, _PROFILES)
    _reject_unknown("test_suite", test_suites, _TEST_SUITES)
    for ov in overrides:
        if not _PKG_RE.match(ov.get("pkg", "")):
            raise ValueError(f"invalid override pkg {ov.get('pkg')!r} (need {{\"pkg\": ..., \"src\": ...}})")
        if not ov.get("src"):
            raise ValueError(f"override {ov['pkg']!r} missing src")
        attrs = ov.get("attrs")
        if attrs is not None and not (isinstance(attrs, dict)
                                      and all(_PKG_RE.match(k) and isinstance(v, str) for k, v in attrs.items())):
            raise ValueError(
                f"override {ov['pkg']!r} attrs must be a dict of {{nixAttr: stringValue}}, e.g. "
                f'{{"autoreconfPhase": "make configure"}}'
            )

    workers = Path(os.environ["WORKERS_DIR"])
    worker_index = os.environ["WORKER_INDEX"]

    # The kdevops-managed VM key is always trusted, additive to any explicit ssh_keys.
    managed = _managed_pubkey()
    if managed:
        ssh_keys = [managed, *(k for k in ssh_keys if k != managed)]
    elif not ssh_keys:
        print("note: no kdevops VM key at system/ssh/id_ed25519.pub (run "
              "f/workbench/init); guest will accept no SSH key", flush=True)

    nixos_flake = vendor_dir(workers) / "nixos-flake"
    template = nixos_flake / "templates/imageless/flake.nix"
    if not template.is_file():
        raise FileNotFoundError(f"imageless template missing at {template} — provision nixos-flake first")

    # Per-VM config dir, hardened against name-based path escapes.
    config_root = workers / worker_index / "nix"
    config_dir = (config_root / vm_name).resolve()
    if config_root.resolve() not in config_dir.parents:
        raise ValueError(f"vm_name {vm_name!r} resolves outside {config_root}")

    flake_text = _render_flake(template, nixos_flake, overrides)
    default_text = _render_default(vm_name, user_name, profiles, test_suites, shares,
                                   overrides, ssh_keys, home_dir if home else "")

    config_dir.mkdir(parents=True, exist_ok=True)
    _emit(config_dir / "flake.nix", flake_text)
    _emit(config_dir / "default.nix", default_text)

    return {
        "config_dir": str(config_dir),
        "flake": str(config_dir / "flake.nix"),
        "default": str(config_dir / "default.nix"),
        "nixos_flake": str(nixos_flake),
        "vm_name": vm_name,
    }


def _managed_pubkey() -> str | None:
    """The kdevops-managed VM public key, baked into every guest's authorizedKeys."""
    pub = system_dir() / "ssh/id_ed25519.pub"
    return pub.read_text().strip() if pub.is_file() else None


def _reject_unknown(kind: str, values: list[str], allowed: set[str]) -> None:
    unknown = [v for v in values if v not in allowed]
    if unknown:
        raise ValueError(f"unknown {kind}(s) {unknown}: choose from {sorted(allowed)}")


def _render_flake(template: Path, nixos_flake: Path, overrides: list[dict]) -> str:
    text = template.read_text()
    if _TEMPLATE_PATH_PLACEHOLDER not in text:
        raise RuntimeError(f"template {template} no longer contains {_TEMPLATE_PATH_PLACEHOLDER!r}")
    text = text.replace(_TEMPLATE_PATH_PLACEHOLDER, f"path:{nixos_flake}", 1)

    if overrides:
        if _FOLLOWS_ANCHOR not in text:
            raise RuntimeError(f"template {template} no longer contains the follows anchor")
        block = "\n\n" + "\n\n".join(_override_input(ov) for ov in overrides)
        text = text.replace(_FOLLOWS_ANCHOR, _FOLLOWS_ANCHOR + block, 1)
    return text


def _override_input(ov: dict) -> str:
    """A `<pkg>-src` non-flake input (path or git), consumed by the default.nix overlay."""
    pkg, src, ref = ov["pkg"], ov["src"], ov.get("ref")
    lines = [f"    {pkg}-src = {{"]
    if src.startswith("/"):
        lines += ['      type = "path";', f"      path = {_nix_str(src)};"]
    else:
        lines += ['      type = "git";', f"      url = {_nix_str(src)};"]
        if ref:
            lines.append(f"      ref = {_nix_str(ref)};")
    lines += ["      flake = false;", "    };"]
    return "\n".join(lines)


def _render_default(
    vm_name: str,
    user_name: str,
    profiles: list[str],
    test_suites: list[str],
    shares: dict,
    overrides: list[dict],
    ssh_keys: list[str],
    root_home: str = "",
) -> str:
    imports = [f"nixos-flake.nixosModules.profiles.{p}" for p in profiles]
    imports += [f"nixos-flake.nixosModules.testSuites.{t}" for t in test_suites]
    if shares:
        imports.append("nixos-flake.nixosModules.mounts.shares")

    out: list[str] = [
        f"# Per-VM overrides for {vm_name}. Generated by kdevops-ng (f/nix/render_config).",
        "#",
        "# The flake's modules list already imports the imageless backend, the user",
        "# module, and the default overlay; this file adds the per-VM composition.",
        "{",
        "  config,",
        "  lib,",
        "  pkgs,",
        "  nixos-flake,",
        "  inputs,",
        "  ...",
        "}:",
        "{",
    ]
    if imports:
        out.append("  imports = [")
        out += [f"    {imp}" for imp in imports]
        out += ["  ];", ""]

    out.append(f"  networking.hostName = {_nix_str(vm_name)};")
    out.append(f"  nixos-flake.user.name = {_nix_str(user_name)};")
    # Land root straight in the operator's home (the mounted `home` share) instead of
    # /root, so `ssh <vm>` drops you into your files. root's uid maps to the operator via
    # virtiofsd, so writes there keep host ownership; no extra guest user is needed.
    # mkForce: the backend module already pins root.home at normal priority.
    if root_home:
        out.append(f"  users.users.root.home = lib.mkForce {_nix_str(root_home)};")
    for prof in profiles:
        opt = _PROFILE_ENABLE.get(prof)
        if opt:
            out.append(f"  {opt} = true;")

    if ssh_keys:
        keys = " ".join(_nix_str(k) for k in ssh_keys)
        out += [
            "",
            f"  users.users.root.openssh.authorizedKeys.keys = [ {keys} ];",
            f"  users.users.{user_name}.openssh.authorizedKeys.keys = [ {keys} ];",
        ]

    if shares:
        out.append("")
        for mount, spec in shares.items():
            opts = spec.get("options")
            opt_str = f" options = [ {' '.join(_nix_str(o) for o in opts)} ];" if opts else ""
            out.append(f"  nixos-flake.shares.{_nix_str(mount)} = {{ tag = {_nix_str(spec['tag'])};{opt_str} }};")

    if overrides:
        out += ["", "  nixpkgs.overlays = lib.mkAfter [", "    (final: prev: {"]
        for ov in overrides:
            # `attrs` carries extra overrideAttrs assignments (string-valued, e.g. a
            # replacement build phase) — needed when a git `src` must build differently
            # than the package's release tarball (e.g. xfsprogs from git wants its own
            # `autoreconfPhase = "make configure"` rather than nixpkgs' generic autoreconf).
            extra = "".join(f" {k} = {_nix_str(v)};" for k, v in (ov.get("attrs") or {}).items())
            out.append(f"      {ov['pkg']} = prev.{ov['pkg']}.overrideAttrs "
                       f"(_: {{ src = inputs.{ov['pkg']}-src;{extra} }});")
        out += ["    })", "  ];"]

    out.append("}")
    return "\n".join(out) + "\n"


def _nix_str(s: str) -> str:
    """Quote a Python string as a Nix double-quoted string (escape \\, ", ${)."""
    esc = s.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${")
    return f'"{esc}"'


def _emit(path: Path, text: str) -> None:
    """Write a generated file and echo it to the job log for auditability."""
    path.write_text(text)
    print(f"+ wrote {path}", flush=True)
    print(text, flush=True)
