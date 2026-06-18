#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Generate f/qsu/bringup.flow/flow.yaml from the subflows it composes
# (f/kernel/build, f/nix/build, f/qemu/build, f/qsu/boot) plus f/qsu/discover. The
# subflows are the single source of truth for their input groups; re-run this after
# changing any of them and commit the regenerated bringup flow.
#
#     python scripts/gen-bringup.py
#
# Each component has a SOURCE selector — kernel/closure: build | reuse; qemu: build |
# reuse | nixpkgs. `build` runs that build subflow; `reuse` takes the artifact from the
# VM named in Reuse from VM (discovered from its render sidecar) or the explicit Reuse
# paths; `nixpkgs` (qemu) boots the store QEMU. All-build is a full bringup; any-reuse
# is a reconfigure that redeploys an existing VM in place (boot uses restart). The form
# flattens each build subflow group to a prefixed top-level group gated on its source.
import copy
import difflib
import os
import sys

import yaml

ROOT = "f"


def load(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


def jx(expr):
    return {"type": "javascript", "expr": expr}


# Dynselect helper for the Reuse from VM dropdown. A flow's dynselect helper runs
# untagged on a default worker (no host bus), so it enumerates the sidecar registry
# WORKERS_DIR/shared/vm/<vm>.vars.json (written by the render step) rather than
# `machinectl`. Returned as the flow schema's x-windmill-dyn-select-code.
DYN_SELECT_CODE = '''import os
from pathlib import Path


def list_reuse_vms(filterText: str = "", **_: object) -> list:
    d = Path(os.environ["WORKERS_DIR"]) / "shared/vm"
    vms = sorted(p.name.removesuffix(".vars.json") for p in d.glob("*.vars.json")) if d.is_dir() else []
    return [{"label": v, "value": v} for v in vms if filterText.lower() in v.lower()]
'''


kernel = load(f"{ROOT}/kernel/build.flow/flow.yaml")
nix = load(f"{ROOT}/nix/build.flow/flow.yaml")
qemu = load(f"{ROOT}/qemu/build.flow/flow.yaml")
boot = load(f"{ROOT}/qsu/boot.flow/flow.yaml")

order = []
props = {}

# --- per-component source selectors -------------------------------------------
props["kernel_source"] = {
    "title": "Kernel",
    "type": "string",
    "description": "`build` runs `f/kernel/build`; `reuse` takes the kernel (image + modules) from the VM in Reuse from VM, else the explicit Reuse paths.",
    "default": "build",
    "enum": ["build", "reuse"],
}
props["closure_source"] = {
    "title": "Closure",
    "type": "string",
    "description": "`build` runs `f/nix/build`; `reuse` takes the NixOS closure (init + initrd) from the VM in Reuse from VM, else the explicit Reuse paths.",
    "default": "build",
    "enum": ["build", "reuse"],
}
props["qemu_source"] = {
    "title": "QEMU",
    "type": "string",
    "description": "`build` runs `f/qemu/build`; `reuse` takes the QEMU the VM in Reuse from VM was built with; `nixpkgs` boots the `qemu` package from `nixpkgs` (via the vendored `nixos-flake`).",
    "default": "nixpkgs",
    "enum": ["build", "reuse", "nixpkgs"],
}
props["reuse_from_vm"] = {
    "title": "Reuse from VM",
    "type": "object",
    "format": "dynselect-list_reuse_vms",
    "description": "Existing VM whose built artifacts to reuse for any component set to `reuse` (reads its render sidecar). Set VM Name to this same name to reconfigure it in place. The dropdown lists VMs that have a render sidecar.",
    "showExpr": 'fields.kernel_source === "reuse" || fields.closure_source === "reuse" || fields.qemu_source === "reuse"',
}
order += ["kernel_source", "closure_source", "qemu_source", "reuse_from_vm"]


# --- build subflows: prefixed top-level groups, gated on the component source ---
def add_build(subflow, prefix, title_prefix, show_expr):
    sch = subflow["schema"]
    transforms = {}
    for gkey in sch["order"]:
        grp = copy.deepcopy(sch["properties"][gkey])
        grp["title"] = title_prefix + grp.get("title", gkey)
        grp["showExpr"] = show_expr
        newkey = f"{prefix}_{gkey}"
        props[newkey] = grp
        order.append(newkey)
        transforms[gkey] = jx(f"flow_input.{prefix}_{gkey}")
    return transforms


kt = add_build(kernel, "kernel", "Kernel: ", 'fields.kernel_source === "build"')
nt = add_build(nix, "nix", "NixOS: ", 'fields.closure_source === "build"')
qt = add_build(qemu, "qemu", "QEMU build: ", 'fields.qemu_source === "build"')

# Bringup builds one VM, not a parallel matrix, so default the kernel/qemu worktrees
# to the shared tree (the standalone builds default per-worker for parallelism). Only
# the bringup form default changes; the build subflows are untouched.
for gk in ("kernel_worker", "qemu_worker"):
    props[gk]["properties"]["shared"]["default"] = True

# The schema default only fills the form; a headless run leaves the group unset and
# falls through to the subflow's per-worker default. Merge shared:true as a floor in
# the transform too (an explicit form value still wins via the spread).
for t, prefix in ((kt, "kernel"), (qt, "qemu")):
    t["worker"] = jx(f"({{ shared: true, ...flow_input.{prefix}_worker }})")

# The closure's hostname + per-VM config dir name default to the booted VM's name, so a
# bringup that only sets the Boot VM Name can't silently render a generic `nixos` closure
# (which would mismatch the guest and miss its test-suite units). home_dir defaults to the
# discovered host operator's home (root login lands there).
nt["guest"] = jx(
    '({...flow_input.nix_guest, '
    'vm_name: (flow_input.nix_guest?.vm_name || flow_input.reuse_from_vm || flow_input.boot_vm?.vm_name), '
    'home_dir: ((flow_input.nix_guest?.home_dir || "").trim() '
    '|| ("/home/" + (results.discover?.host_user || "kdevops")))})'
)

# --- boot subflow: re-expose groups, drop the wired/derived fields ------------
bsch = boot["schema"]["properties"]

# qemu runtime group minus the source/binary (derived from qemu_source + results/discover)
boot_qemu = copy.deepcopy(bsch["qemu"])
for k in ("qemu_source", "qemu_binary"):
    boot_qemu["properties"].pop(k, None)
    if k in boot_qemu.get("order", []):
        boot_qemu["order"].remove(k)

# sharing group minus modules_dir (moves to Reuse; reuse infers it from the kernel manifest)
boot_sharing = copy.deepcopy(bsch["sharing"])
modules_dir_def = boot_sharing["properties"].pop("modules_dir", None)
if "modules_dir" in boot_sharing.get("order", []):
    boot_sharing["order"].remove("modules_dir")

for newkey, src, title in [
    ("boot_vm", bsch["vm"], "VM"),
    ("boot_qemu", boot_qemu, "QEMU"),
    ("boot_networking", bsch["networking"], "Networking"),
    ("boot_sharing", boot_sharing, "File sharing"),
    ("boot_nvme", bsch["nvme"], "NVMe"),
    ("boot_orchestration", bsch["orchestration"], "Orchestration"),
]:
    grp = copy.deepcopy(src)
    grp["title"] = title
    props[newkey] = grp
    order.append(newkey)

# Reuse overrides: explicit kernel/closure paths, used when a component is `reuse` but
# the discovered VM sidecar lacks them (or no VM is named).
kb = bsch["kernel_boot"]["properties"]
reuse = {
    "title": "Reuse",
    "type": "object",
    "description": "Explicit kernel/closure paths for a `reuse` component, used when not discovered from Reuse from VM.",
    "default": {},
    "showExpr": 'fields.kernel_source === "reuse" || fields.closure_source === "reuse"',
    "order": ["kernel_image", "kernel_initrd", "kernel_append", "modules_dir"],
    "properties": {
        "kernel_image": copy.deepcopy(kb["kernel_image"]),
        "kernel_initrd": copy.deepcopy(kb["kernel_initrd"]),
        "kernel_append": copy.deepcopy(kb["kernel_append"]),
        "modules_dir": copy.deepcopy(modules_dir_def),
    },
}
props["reuse"] = reuse
order.append("reuse")

# --- boot step input_transforms: pick each artifact by its source -------------
# build -> the build subflow's result; reuse -> the discover step's sidecar manifest
# (explicit Reuse paths still override via the kernel_image/initrd/append fields);
# qemu nixpkgs -> no binary (boot resolves the store qemu).
boot_transforms = {
    "vm": jx("flow_input.boot_vm"),
    "networking": jx("flow_input.boot_networking"),
    "nvme": jx("flow_input.boot_nvme"),
    "orchestration": jx("flow_input.boot_orchestration"),
    "qemu": jx(
        '({...flow_input.boot_qemu, '
        'qemu_source: flow_input.qemu_source === "build" ? "qemu-build" '
        ': (flow_input.qemu_source === "reuse" ? results.discover?.qemu_source : "nixpkgs"), '
        'qemu_binary: flow_input.qemu_source === "build" ? results.build_qemu?.qemu_binary '
        ': (flow_input.qemu_source === "reuse" ? results.discover?.qemu_binary : null)})'
    ),
    # reuse closure -> replay the shares recorded in the sidecar (explicit File-Sharing
    # overrides on top); build -> derive fstests/home from the closure inputs. A pre-fix
    # sidecar has no `sharing` block (discover returns an empty `{}`): the empty test below
    # falls through to the BUILD-derive branch so shares are reconstructed from the closure
    # inputs rather than replayed from an empty base (which would drop fstests/home).
    "sharing": jx(
        '(flow_input.closure_source === "reuse" && results.discover?.sharing && Object.keys(results.discover.sharing).length > 0 '
        '? ({ ...results.discover.sharing, ...flow_input.boot_sharing, modules_dir: flow_input.reuse?.modules_dir }) '
        ': ({ ...flow_input.boot_sharing, modules_dir: flow_input.reuse?.modules_dir, '
        'fstests: (flow_input.nix_closure?.test_suites || []).includes("fstests"), '
        'home_share: (flow_input.nix_guest?.home === true), '
        'home_share_readwrite: (flow_input.nix_guest?.home === true) }))'
    ),
    "kernel_boot": jx(
        '({kernel: flow_input.kernel_source === "build" ? results.build_kernel : results.discover?.kernel, '
        'closure: flow_input.closure_source === "build" ? results.build_nix : results.discover?.closure, '
        "kernel_image: flow_input.reuse?.kernel_image, kernel_initrd: flow_input.reuse?.kernel_initrd, "
        "kernel_append: flow_input.reuse?.kernel_append})"
    ),
}

modules = [
    {"id": "discover", "summary": "Discover reuse artifacts + host operator (f/qsu/discover)",
     "value": {"type": "script", "path": "f/qsu/discover",
               "input_transforms": {"vm_name": jx("flow_input.reuse_from_vm || flow_input.boot_vm?.vm_name")}}},
    {"id": "build_kernel", "summary": "Build the kernel (f/kernel/build)",
     "skip_if": {"expr": 'flow_input.kernel_source !== "build"'},
     "value": {"type": "flow", "path": "f/kernel/build", "input_transforms": kt}},
    {"id": "build_nix", "summary": "Build the NixOS closure (f/nix/build)",
     "skip_if": {"expr": 'flow_input.closure_source !== "build"'},
     "value": {"type": "flow", "path": "f/nix/build", "input_transforms": nt}},
    {"id": "build_qemu", "summary": "Build QEMU (f/qemu/build)",
     "skip_if": {"expr": 'flow_input.qemu_source !== "build"'},
     "value": {"type": "flow", "path": "f/qemu/build", "input_transforms": qt}},
    {"id": "boot", "summary": "Render + boot the VM (f/qsu/boot)",
     "value": {"type": "flow", "path": "f/qsu/boot", "input_transforms": boot_transforms}},
]

bringup = {
    "summary": "QEMU/systemd VM Bringup",
    "description": (
        "Build and/or reuse a kernel, a NixOS closure and QEMU, then boot a VM from them. "
        "Each component has a source: kernel/closure are build|reuse, QEMU is build|reuse|nixpkgs. "
        "`build` runs the build subflow and boots its result; `reuse` takes the artifact from the "
        "VM named in Reuse from VM (discovered from its render sidecar, no rebuild); QEMU `nixpkgs` "
        "boots the store QEMU. All-build is a full pipeline; set a component to `reuse` (and VM Name "
        "to that VM) to reconfigure an existing VM in place — boot restarts it with the new render. "
        "Generated by scripts/gen-bringup.py from the subflows; do not hand-edit."
    ),
    "value": {"modules": modules},
    "schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "x-windmill-dyn-select-code": DYN_SELECT_CODE,
        "x-windmill-dyn-select-lang": "python3",
        "order": order,
        "properties": props,
        "required": [],
    },
}

dest = f"{ROOT}/qsu/bringup.flow/flow.yaml"
text = yaml.safe_dump(bringup, default_flow_style=False, sort_keys=False, width=4096, allow_unicode=True)

if "--check" in sys.argv[1:]:
    try:
        on_disk = open(dest).read()
    except FileNotFoundError:
        on_disk = None
    if on_disk == text:
        print(f"OK: {dest} is up to date")
        sys.exit(0)
    diff = difflib.unified_diff(
        (on_disk or "").splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile=f"{dest} (on disk)", tofile="gen-bringup.py (regenerated)",
    )
    sys.stderr.write("".join(diff))
    sys.stderr.write(
        f"\n{dest} is stale: edit the subflows + scripts/gen-bringup.py, "
        "then run `python3 scripts/gen-bringup.py`. Never hand-edit the generated flow.\n"
    )
    sys.exit(1)

os.makedirs(os.path.dirname(dest), exist_ok=True)
with open(dest, "w") as fh:
    fh.write(text)
print(f"wrote {dest}: {len(order)} top-level groups/toggles, {len(modules)} steps")
