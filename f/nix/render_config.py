# SPDX-License-Identifier: copyleft-next-0.3.1
"""Render a per-VM imageless NixOS configuration: flake.nix + default.nix.

This is the Windmill equivalent of kdevops's `nixosfi` generate-configs phase. The
flake.nix is a near-verbatim copy of the vendored imageless *template* (only the
`nixos-flake` path input is set, plus one `<pkg>-src` input per source override);
default.nix is generated from the typed inputs and carries the per-VM composition
(which profiles/testSuites/mounts to import, hostname, SSH keys, source overrides).
The flake's own modules list already imports the imageless backend, the user module,
and the default overlay, so default.nix only adds to that.

Both files are written under `$WORKERS_DIR/$WORKER_INDEX/nix/<vm_name>/`: a
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
from f.common.gitrefs import qualify_ref

# Composable nixos-flake module attributes (see vendor/nixos-flake/flake.nix).
_PROFILES = {"build-tools", "controller", "devel", "monitoring", "telemetry"}
_TEST_SUITES = [
    "blktests",
    "fstests",
    "gitr",
    "kunit",
    "ltp",
    "mmtests",
    "pynfs",
    "selftests",
    "sysbench",
    "usertests",
]

# Packages whose nixos-flake recipe a src override composes with, build-verified
# from a git checkout: fio, xfstests and xfsprogs (overlays), libbpf-tools (custom
# pkg, src from iovisor/bcc), and blktests (custom pkg, carries the scope patch).
# Packages for other suites (spdk, xnvme, nfstest, pynfs, ...) join as verified.
# The advanced `extra_overrides` takes any other nixpkgs package.
_OVERRIDABLE_PKGS = ["fio", "xfstests", "xfsprogs", "libbpf-tools", "blktests"]

# The mirror project whose Bare carries each overridable package's source
# (f/workbench/fetch cuts one Bare per project under $SYSTEM_DIR/bare).
_PKG_PROJECTS = {
    "fio": "fio",
    "xfstests": "xfstests-dev",
    "xfsprogs": "xfsprogs-dev",
    "libbpf-tools": "bcc",
    "blktests": "blktests",
}

# nixpkgs builds these from a release tarball that ships a prepared `./configure`; a
# raw source tree (a path or git override) has none, so xfsprogs needs its own
# autoreconf. Attached automatically when the package is overridden from source, so the
# curated form only has to ask for the source.
_PKG_SOURCE_ATTRS = {
    "xfsprogs": {"autoreconfPhase": "make configure"},
}

# nixpkgs pins release-specific fixes as `patches`; a source override builds a
# tree that usually already contains them, and `patch` bails out ("previously
# applied"), so the list resets. Values are raw Nix, emitted unquoted.
# blktests and xfstests keep their carried patches on purpose: the vendored
# recipes apply them to any src.
_PKG_SOURCE_RAW_ATTRS = {
    "fio": {"patches": "[ ]"},
}


def _source_overrides_to_list(source_overrides: dict | None) -> list[dict]:
    """The curated per-package ref form (`{pkg: {ref}}`) as override rows.

    Each overridable package (`_OVERRIDABLE_PKGS`) has one `ref` in the form: a
    branch, tag or commit in its project's Bare (`_PKG_PROJECTS`); a package with
    a blank ref keeps its pinned version. The known extra build attrs a raw tree
    needs (`_PKG_SOURCE_ATTRS`) are attached automatically. Returns the rows in
    `_OVERRIDABLE_PKGS` order, so the primary input is a form, never a
    hand-composed JSON array.
    """
    out: list[dict] = []
    for pkg in _OVERRIDABLE_PKGS:
        spec = (source_overrides or {}).get(pkg) or {}
        ref = str(spec.get("ref", "") or "").strip()
        if not ref:
            continue
        ov: dict = {"pkg": pkg, "project": _PKG_PROJECTS[pkg], "ref": ref}
        attrs = _PKG_SOURCE_ATTRS.get(pkg)
        if attrs:
            ov["attrs"] = dict(attrs)
        raw = _PKG_SOURCE_RAW_ATTRS.get(pkg)
        if raw:
            ov["raw_attrs"] = dict(raw)
        out.append(ov)
    return out


# Profiles whose effect is behind an enable gate: importing alone is inert, so we
# turn them on when selected. devel and build-tools are active on import. controller
# is a host role (it pulls in libvirtd) and telemetry pushes to an external
# monitoring stack, so both are excluded from the featured default.
_PROFILE_ENABLE = {
    "monitoring": "nixos-flake.monitoring.enable",
    "controller": "nixos-flake.controller.enable",
    "telemetry": "nixos-flake.telemetry.enable",
}

# node_exporter collectors the telemetry profile can enable on top of its
# defaults (nixos-flake.telemetry.extraCollectors); all are off in
# node_exporter by default.
# ebpf_exporter example configs the telemetry profile can load; each is a
# CO-RE BPF object beside its YAML in the package.
_TELEMETRY_EBPF_CONFIGS = ["biolatency", "bio-trace"]

_TELEMETRY_COLLECTORS = [
    "buddyinfo",
    "zoneinfo",
    "meminfo_numa",
    "processes",
    "interrupts",
]

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
    # 10.0.2.2 is the QEMU user-net alias for the host (slirp maps it to the host
    # loopback), so the defaults reach a monitoring stack on the VM's own host;
    # baremetal or cross-host guests pass explicit URLs.
    telemetry_metrics_url: str = "http://10.0.2.2:9090/api/v1/write",
    telemetry_logs_url: str = "http://10.0.2.2:3100/loki/api/v1/push",
    telemetry_collectors: list[str] | None = None,
    telemetry_ebpf: bool = False,
    telemetry_ebpf_configs: list[str] | None = None,
    shares: dict | None = None,
    source_overrides: dict | None = None,
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
    profiles = [
        p for p in (profiles if profiles is not None else _FEATURED_PROFILES) if p
    ]
    test_suites = [
        t
        for t in (test_suites if test_suites is not None else _FEATURED_TEST_SUITES)
        if t
    ]
    telemetry_metrics_url = telemetry_metrics_url or "http://10.0.2.2:9090/api/v1/write"
    telemetry_logs_url = telemetry_logs_url or "http://10.0.2.2:3100/loki/api/v1/push"
    telemetry_collectors = [c for c in (telemetry_collectors or []) if c]
    telemetry_ebpf_configs = [c for c in (telemetry_ebpf_configs or []) if c] or [
        "biolatency"
    ]
    # The curated form gives each overridable package a ref picker off its
    # project's Bare, so a user builds a branch without composing JSON;
    # extra_overrides is the gated advanced escape for any other nixpkgs package
    # (a raw src: an absolute path, or a git URL). Validated below.
    overrides = _source_overrides_to_list(source_overrides)
    overrides += [ov for ov in (extra_overrides or []) if isinstance(ov, dict) and ov]
    ssh_keys = [k for k in (ssh_keys or []) if k and k.strip()]
    shares = {
        m: s
        for m, s in (shares or {}).items()
        if m and isinstance(s, dict) and s.get("tag")
    }
    # Predefined shares the operator should not have to declare by hand (they coexist
    # with the free-form `shares` above; an explicit entry for the same mount wins).
    # The matching host-served share is composed by f/qsu (qsu/common._shares).
    #  - fstests: auto whenever the closure runs the fstests suite.
    if "fstests" in test_suites:
        shares.setdefault("/var/lib/xfstests", {"tag": "fstests"})
    #  - selftests: auto whenever the closure runs the selftests suite.
    if "selftests" in test_suites:
        shares.setdefault("/var/lib/kselftests", {"tag": "selftests"})
    #  - usertests: auto whenever the closure runs the usertests suite.
    if "usertests" in test_suites:
        shares.setdefault("/var/lib/usertests", {"tag": "usertests"})
    #  - blktests: auto whenever the closure runs the blktests suite.
    if "blktests" in test_suites:
        shares.setdefault("/var/lib/blktests", {"tag": "blktests"})
    #  - home: the operator's host home (tag `home`, served once by qsu) mounted at
    #    /home/<operator> AND set as root's home (below), so `ssh <vm>` lands you straight
    #    in your home (writable via the root->operator virtiofsd uid-map, with no extra
    #    guest user and no sandbox change). A flow transform can't read the filesystem, so
    #    the path is resolved here; bringup passes /home/<host_user> from resolve.
    home_dir = (home_dir or "").strip()
    if home and not home_dir:
        h = os.environ.get("HOME", "")
        home_dir = h if h.startswith("/home/") else "/home/kdevops"
    if home:
        shares.setdefault(home_dir, {"tag": "home"})

    if not _VM_NAME_RE.match(vm_name):
        raise ValueError(
            f"invalid vm_name {vm_name!r}: must match {_VM_NAME_RE.pattern}"
        )
    _reject_unknown("profile", profiles, _PROFILES)
    _reject_unknown("test_suite", test_suites, _TEST_SUITES)
    _reject_unknown(
        "telemetry collector", telemetry_collectors, set(_TELEMETRY_COLLECTORS)
    )
    _reject_unknown(
        "telemetry ebpf config", telemetry_ebpf_configs, set(_TELEMETRY_EBPF_CONFIGS)
    )
    seen_pkgs: set[str] = set()
    for ov in overrides:
        if not _PKG_RE.match(ov.get("pkg", "")):
            raise ValueError(
                f'invalid override pkg {ov.get("pkg")!r} (need {{"pkg": ..., "src": ...}})'
            )
        if ov["pkg"] in seen_pkgs:
            raise ValueError(
                f"package {ov['pkg']!r} overridden twice; the form ref and an "
                "extra_overrides row name the same package"
            )
        seen_pkgs.add(ov["pkg"])
        if "project" not in ov and not ov.get("src"):
            raise ValueError(f"override {ov['pkg']!r} missing src")
        if "raw_attrs" in ov and "project" not in ov:
            raise ValueError(
                f"override {ov['pkg']!r}: raw_attrs is internal; use attrs"
            )
        attrs = ov.get("attrs")
        if attrs is not None and not (
            isinstance(attrs, dict)
            and all(_PKG_RE.match(k) and isinstance(v, str) for k, v in attrs.items())
        ):
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
        print(
            "note: no kdevops VM key at system/ssh/id_ed25519.pub (run "
            "f/workbench/init); guest will accept no SSH key",
            flush=True,
        )

    nixos_flake = vendor_dir(workers) / "nixos-flake"
    template = nixos_flake / "templates/imageless/flake.nix"
    if not template.is_file():
        raise FileNotFoundError(
            f"imageless template missing at {template}; provision nixos-flake first"
        )

    # Per-VM config dir, hardened against name-based path escapes.
    config_root = workers / worker_index / "nix"
    config_dir = (config_root / vm_name).resolve()
    if config_root.resolve() not in config_dir.parents:
        raise ValueError(f"vm_name {vm_name!r} resolves outside {config_root}")

    flake_text = _render_flake(template, nixos_flake, overrides)
    default_text = _render_default(
        vm_name,
        user_name,
        profiles,
        test_suites,
        telemetry_metrics_url,
        telemetry_logs_url,
        telemetry_collectors,
        telemetry_ebpf,
        telemetry_ebpf_configs,
        shares,
        overrides,
        ssh_keys,
        home_dir if home else "",
    )

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
        raise RuntimeError(
            f"template {template} no longer contains {_TEMPLATE_PATH_PLACEHOLDER!r}"
        )
    text = text.replace(_TEMPLATE_PATH_PLACEHOLDER, f"path:{nixos_flake}", 1)

    if overrides:
        if _FOLLOWS_ANCHOR not in text:
            raise RuntimeError(
                f"template {template} no longer contains the follows anchor"
            )
        block = "\n\n" + "\n\n".join(_override_input(ov) for ov in overrides)
        text = text.replace(_FOLLOWS_ANCHOR, _FOLLOWS_ANCHOR + block, 1)
    return text


# A full commit id pins the input by rev; anything else resolves as a ref.
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _bare_dir(project: str) -> Path:
    bare = system_dir() / "bare" / f"{project}.git"
    if not (bare / "objects").is_dir():
        raise FileNotFoundError(
            f"no Bare for {project} at {bare}; run f/workbench/init first"
        )
    return bare


def _qualified_ref(project: str, ref: str) -> str:
    qualified = qualify_ref(project, ref)
    if not qualified:
        raise ValueError(
            f"ref {ref!r} not found in the {project} Bare (tried refs/tags, "
            "refs/remotes/mirror, refs/heads, refs/remotes); push the branch "
            "to the Bare or refresh the mirror (f/workbench/fetch)"
        )
    return qualified


def _override_input(ov: dict) -> str:
    """A `<pkg>-src` non-flake input, consumed by the default.nix overlay.

    A curated row (`project` + `ref`) clones the project's host-local Bare at
    the fully qualified ref, so every source a worker builds passed through
    the Bare; an `extra_overrides` row keeps its raw `src` (an absolute path,
    or a git URL) verbatim.
    """
    pkg = ov["pkg"]
    lines = [f"    {pkg}-src = {{"]
    if "project" in ov:
        project, ref = ov["project"], ov["ref"]
        url = f"file://{_bare_dir(project)}"
        lines += ['      type = "git";', f"      url = {_nix_str(url)};"]
        if _FULL_SHA_RE.match(ref):
            lines.append(f"      rev = {_nix_str(ref)};")
        else:
            lines.append(f"      ref = {_nix_str(_qualified_ref(project, ref))};")
        # bcc (libbpf-tools) vendors libbpf/bpftool/blazesym as submodules a
        # build needs; their .gitmodules URLs are absolute, so they fetch from
        # upstream. Harmless for repos that have none.
        lines.append("      submodules = true;")
    elif ov["src"].startswith("/"):
        lines += ['      type = "path";', f"      path = {_nix_str(ov['src'])};"]
    else:
        lines += ['      type = "git";', f"      url = {_nix_str(ov['src'])};"]
        ref = ov.get("ref")
        if ref:
            lines.append(f"      ref = {_nix_str(ref)};")
        lines.append("      submodules = true;")
    lines += ["      flake = false;", "    };"]
    return "\n".join(lines)


def _render_default(
    vm_name: str,
    user_name: str,
    profiles: list[str],
    test_suites: list[str],
    telemetry_metrics_url: str,
    telemetry_logs_url: str,
    telemetry_collectors: list[str],
    telemetry_ebpf: bool,
    telemetry_ebpf_configs: list[str],
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
        # The shared home carries the host's systemd user units and their enable
        # symlinks; a guest user manager rooted in that home would start them (host
        # mirror timers, workers). An empty read-only tmpfs over .config/systemd
        # keeps host units host-only while the rest of the home stays shared.
        out.append(
            f"  fileSystems.{_nix_str(root_home + '/.config/systemd')} = {{ "
            'fsType = "tmpfs"; options = [ "ro" "nosuid" "nodev" "mode=0555" ]; };'
        )
    for prof in profiles:
        opt = _PROFILE_ENABLE.get(prof)
        if opt:
            out.append(f"  {opt} = true;")
        if prof == "telemetry":
            out.append(
                "  nixos-flake.telemetry.metrics.url = "
                f"{_nix_str(telemetry_metrics_url)};"
            )
            out.append(
                f"  nixos-flake.telemetry.logs.url = {_nix_str(telemetry_logs_url)};"
            )
            if telemetry_collectors:
                cols = " ".join(_nix_str(c) for c in telemetry_collectors)
                out.append(f"  nixos-flake.telemetry.extraCollectors = [ {cols} ];")
            if telemetry_ebpf:
                out.append("  nixos-flake.telemetry.ebpf.enable = true;")
                cfgs = " ".join(_nix_str(c) for c in telemetry_ebpf_configs)
                out.append(f"  nixos-flake.telemetry.ebpf.configs = [ {cfgs} ];")

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
            opt_str = (
                f" options = [ {' '.join(_nix_str(o) for o in opts)} ];" if opts else ""
            )
            out.append(
                f"  nixos-flake.shares.{_nix_str(mount)} = {{ tag = {_nix_str(spec['tag'])};{opt_str} }};"
            )

    if overrides:
        out += ["", "  nixpkgs.overlays = lib.mkAfter [", "    (final: prev: {"]
        for ov in overrides:
            pkg = ov["pkg"]
            # `attrs` carries extra overrideAttrs assignments (string-valued, e.g. a
            # replacement build phase): needed when a git `src` must build differently
            # than the package's release tarball (e.g. xfsprogs from git wants its own
            # `autoreconfPhase = "make configure"` rather than nixpkgs' generic autoreconf).
            extra = "".join(
                f" {k} = {_nix_str(v)};" for k, v in (ov.get("attrs") or {}).items()
            )
            extra += "".join(
                f" {k} = {v};" for k, v in (ov.get("raw_attrs") or {}).items()
            )
            if "project" in ov:
                # Stamp the source rev into version and name, so the build
                # log, the store path and the guest all show which commit the
                # package was built from.
                stamp = (
                    f" version = prev.{pkg}.version"
                    f' + "+git" + (inputs.{pkg}-src.shortRev or "src");'
                    f' name = "{pkg}-" + version;'
                )
                out.append(
                    f"      {pkg} = prev.{pkg}.overrideAttrs "
                    f"(_: rec {{ src = inputs.{pkg}-src;{stamp}{extra} }});"
                )
            else:
                out.append(
                    f"      {pkg} = prev.{pkg}.overrideAttrs "
                    f"(_: {{ src = inputs.{pkg}-src;{extra} }});"
                )
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
