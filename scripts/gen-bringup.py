#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Generate f/qsu/bringup.flow/flow.yaml from the subflows it composes
# (f/kernel/build, f/nix/build, f/qemu/build, f/qsu/boot) plus f/qsu/resolve. The
# subflows are the single source of truth for their input groups; re-run this after
# changing any of them and commit the regenerated bringup flow.
#
#     python3 scripts/gen-bringup.py
#
# Each artifact component (Kernel, NixOS closure, QEMU) is ONE group whose first field
# is a `mode` selector; its build sub-groups and reuse picker are siblings gated by
# showExpr on `mode` (Windmill resolves showExpr `fields.X` against the enclosing
# object). Kernel/QEMU reuse picks an artifact from this host's Nix-store index
# (kernel-<release>/qemu-<identity>); closure reuse replays a refreshed VM's recorded
# init/initrd. A final VM group selects the target: a new VM, or refresh a deployed one
# in place (boot uses `systemctl --user restart`). The artifact source and the VM target are
# orthogonal: pick a new kernel from the store AND refresh an existing VM with it.
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


# Flow dynselect helpers. A flow's dynselect runs untagged on a default worker (no host
# bus), so the kernel/qemu pickers enumerate this host's store index (store.list_index,
# which already reflects fetched peer artifacts) and the VM picker the sidecar registry
# WORKERS_DIR/shared/vm/<vm>.vars.json. Returned as the flow schema's
# x-windmill-dyn-select-code.
DYN_SELECT_CODE = """import os
from pathlib import Path


def list_kernel_index(filterText: str = "", **_: object) -> list:
    from f.common import store

    return [
        {"label": n, "value": n}
        for n in store.list_index("kernel-")
        if not n.startswith("kernel-devel-") and filterText.lower() in n.lower()
    ]


def list_qemu_index(filterText: str = "", **_: object) -> list:
    from f.common import store

    return [
        {"label": n, "value": n}
        for n in store.list_index("qemu-")
        if filterText.lower() in n.lower()
    ]


def list_deployed_vms(filterText: str = "", **_: object) -> list:
    d = Path(os.environ["WORKERS_DIR"]) / "shared/vm"
    vms = sorted(p.name.removesuffix(".vars.json") for p in d.glob("*.vars.json")) if d.is_dir() else []
    return [{"label": v, "value": v} for v in vms if filterText.lower() in v.lower()]


def list_iommu(filterText: str = "", **_: object) -> list:
    # Bringup boots a build/reuse/nixpkgs qemu, but only nixpkgs is guaranteed to
    # exist at form-fill time (build runs later in the flow), so query it for the
    # vIOMMU set. Device names are stable across qemu builds and the template
    # constrains to the supported four regardless.
    from f.qsu.binaries import iommu_options
    return iommu_options({}, filterText)
"""


kernel = load(f"{ROOT}/kernel/build.flow/flow.yaml")
nix = load(f"{ROOT}/nix/build.flow/flow.yaml")
qemu = load(f"{ROOT}/qemu/build.flow/flow.yaml")
boot = load(f"{ROOT}/qsu/boot.flow/flow.yaml")

order = []
props = {}


# --- artifact components: one group, mode-gated build sub-groups + reuse picker -------
def add_component(
    subflow, name, title, modes, default_mode, mode_desc, picker, overrides
):
    """Emit the `name` component group and return its build-subflow input_transforms.

    `picker` is `{key, title, format, desc}` for the reuse dropdown (or None); `overrides`
    is a dict of extra reuse-only scalar props (the explicit kernel paths). Every build
    sub-group, the picker and the overrides carry a showExpr on the group's `mode` so the
    form shows only what the selected mode needs.
    """
    sub = subflow["schema"]
    gprops = {
        "mode": {
            "title": title,
            "type": "string",
            "description": mode_desc,
            "default": default_mode,
            "enum": modes,
        }
    }
    gorder = ["mode"]
    if picker:
        gprops[picker["key"]] = {
            "title": picker["title"],
            "type": "string",
            "format": picker["format"],
            "description": picker["desc"],
            "default": "",
            "showExpr": 'fields.mode === "reuse"',
        }
        gorder.append(picker["key"])
    for okey, oval in (overrides or {}).items():
        ov = copy.deepcopy(oval)
        ov["showExpr"] = 'fields.mode === "reuse"'
        gprops[okey] = ov
        gorder.append(okey)
    transforms = {}
    for gkey in sub["order"]:
        grp = copy.deepcopy(sub["properties"][gkey])
        grp["showExpr"] = 'fields.mode === "build"'
        gprops[gkey] = grp
        gorder.append(gkey)
        transforms[gkey] = jx(f"flow_input.{name}?.{gkey}")
    props[name] = {
        "type": "object",
        "title": title,
        "default": {},
        "order": gorder,
        "properties": gprops,
    }
    order.append(name)
    return transforms


# The Kernel component is the kernel artifact only (build it, or pick a built one). The
# boot-time fields (image path, initrd, cmdline, modules dir) are not kernel config: the
# run layer carries the modules with the image, and the initrd/cmdline come from the
# closure. So drop modules_dir from the File-sharing group too; it is reconstructed from
# the kernel manifest at boot.
bsch = boot["schema"]["properties"]
boot_sharing = copy.deepcopy(bsch["sharing"])
boot_sharing["properties"].pop("modules_dir", None)
if "modules_dir" in boot_sharing.get("order", []):
    boot_sharing["order"].remove("modules_dir")

kt = add_component(
    kernel,
    "kernel",
    "Kernel",
    ["build", "reuse"],
    "build",
    "`build` runs `f/kernel/build`; `reuse` selects a built kernel (image + modules together) from this host's Nix-store index. The initrd and kernel cmdline are not set here: they come from the closure.",
    {
        "key": "kernel_pick",
        "title": "Reuse kernel",
        "format": "dynselect-list_kernel_index",
        "desc": "Built kernel run layer to boot (`kernel-<release>` in the store index, local or fetched from a peer).",
    },
    None,
)
ct = add_component(
    nix,
    "closure",
    "NixOS closure",
    ["build", "reuse"],
    "build",
    "`build` runs `f/nix/build` (Nix content-addresses it, so an unchanged profiles/test-suites set rebuilds instantly); `reuse` replays the `init`/`initrd` and shares the Refresh VM recorded (set the VM group to refresh that VM).",
    None,
    None,
)
qt = add_component(
    qemu,
    "qemu",
    "QEMU",
    ["build", "reuse", "nixpkgs"],
    "nixpkgs",
    "`build` runs `f/qemu/build`; `reuse` boots a QEMU picked from this host's Nix-store index; `nixpkgs` boots the `qemu` package from `nixpkgs` (via the vendored `nixos-flake`).",
    {
        "key": "qemu_pick",
        "title": "Reuse QEMU",
        "format": "dynselect-list_qemu_index",
        "desc": "Published QEMU install tree to boot (`qemu-<identity>` in the store index, local or fetched from a peer).",
    },
    None,
)

# Each build lands in its worker's warm `main` tree and publishes its run layer to the
# Nix store; the boot step (a different worker group) resolves that store path from the
# build manifest, so bringup needs no shared tree to hand artifacts across groups.

# Pin test_suites so the closure and the boot share derivation default an omitted value
# the same way (else render_config defaults to every suite, mounting the fstests share,
# while the boot provides none, and the guest hangs on the missing mount).
ct["closure"] = jx(
    "({...flow_input.closure?.closure, test_suites: flow_input.closure?.closure?.test_suites ?? []})"
)

# The closure's hostname + per-VM config dir name default to the VM target's name, so a
# bringup that only sets the VM target can't silently render a generic `nixos` closure
# (which would mismatch the guest and miss its test-suite units). home_dir defaults to the
# resolved host operator's home (root login lands there).
ct["guest"] = jx(
    "({...flow_input.closure?.guest, "
    'vm_name: (flow_input.closure?.guest?.vm_name || (flow_input.vm?.vm_target === "refresh" ? flow_input.vm?.refresh_vm : flow_input.vm?.vm_name)), '
    'home_dir: ((flow_input.closure?.guest?.home_dir || "").trim() '
    '|| ("/home/" + (results.resolve?.host_user || "kdevops")))})'
)

# --- boot subflow: re-expose runtime groups, drop the wired/derived fields ----------
# qemu runtime group minus the source/binary (derived from the QEMU component mode)
boot_qemu = copy.deepcopy(bsch["qemu"])
boot_qemu["title"] = "QEMU machine"
for k in ("qemu_source", "qemu_binary"):
    boot_qemu["properties"].pop(k, None)
    if k in boot_qemu.get("order", []):
        boot_qemu["order"].remove(k)

for newkey, src, title in [
    ("boot_qemu", boot_qemu, "QEMU machine"),
    ("boot_networking", bsch["networking"], "Networking"),
    ("boot_sharing", boot_sharing, "File sharing"),
    ("boot_nvme", bsch["nvme"], "NVMe"),
    ("boot_orchestration", bsch["orchestration"], "Orchestration"),
]:
    grp = copy.deepcopy(src)
    grp["title"] = title
    props[newkey] = grp
    order.append(newkey)

# --- VM target group (final): new VM or refresh a deployed one in place -------------
bvm = bsch["vm"]["properties"]
auto_vm_name = copy.deepcopy(bvm["auto_vm_name"])
auto_vm_name["showExpr"] = 'fields.vm_target === "new"'
vm_name = copy.deepcopy(bvm["vm_name"])
vm_name["showExpr"] = 'fields.vm_target === "new" && fields.auto_vm_name === false'
props["vm"] = {
    "type": "object",
    "title": "VM",
    "description": "Deploy target: a fresh VM, or refresh a deployed one in place (re-render with the selected kernel/QEMU/closure and restart).",
    "default": {},
    "order": ["vm_target", "refresh_vm", "auto_vm_name", "vm_name"],
    "properties": {
        "vm_target": {
            "title": "Target",
            "type": "string",
            "description": "`new` boots a fresh VM; `refresh` re-renders a deployed VM (its name, ports and CID kept) and restarts it with the new artifacts.",
            "default": "new",
            "enum": ["new", "refresh"],
        },
        "refresh_vm": {
            "title": "Refresh VM",
            "type": "string",
            "format": "dynselect-list_deployed_vms",
            "description": "Deployed VM to refresh (the render-sidecar registry).",
            "default": "",
            "showExpr": 'fields.vm_target === "refresh"',
        },
        "auto_vm_name": auto_vm_name,
        "vm_name": vm_name,
    },
}
order.append("vm")

# --- boot step input_transforms: pick each artifact by its component mode -----------
# build -> the build subflow's result; reuse -> the resolve step (store kernel/qemu,
# sidecar closure); qemu nixpkgs -> no binary (boot resolves the store qemu).
boot_transforms = {
    "vm": jx(
        '({vm_name: (flow_input.vm?.vm_target === "refresh" ? flow_input.vm?.refresh_vm : flow_input.vm?.vm_name), '
        'auto_vm_name: (flow_input.vm?.vm_target === "refresh" ? false : flow_input.vm?.auto_vm_name)})'
    ),
    "networking": jx("flow_input.boot_networking"),
    "nvme": jx("flow_input.boot_nvme"),
    "orchestration": jx("flow_input.boot_orchestration"),
    "qemu": jx(
        "({...flow_input.boot_qemu, "
        'qemu_source: (flow_input.qemu?.mode === "build" || flow_input.qemu?.mode === "reuse") ? "qemu-build" : "nixpkgs", '
        'qemu_binary: flow_input.qemu?.mode === "build" ? results.build_qemu?.qemu_binary '
        ': (flow_input.qemu?.mode === "reuse" ? results.resolve?.qemu_binary : null)})'
    ),
    # reuse closure -> replay the shares recorded in the sidecar (explicit File-Sharing
    # overrides on top); build -> derive fstests/home from the closure inputs. An empty
    # resolve sharing (no reuse / pre-fix sidecar) falls through to the BUILD-derive branch
    # so shares are reconstructed from the closure inputs rather than dropped.
    "sharing": jx(
        '(flow_input.closure?.mode === "reuse" && results.resolve?.sharing && Object.keys(results.resolve.sharing).length > 0 '
        "? ({ ...results.resolve.sharing, ...flow_input.boot_sharing }) "
        ": ({ ...flow_input.boot_sharing, "
        'fstests: (flow_input.closure?.closure?.test_suites || []).includes("fstests"), '
        "home_share: (flow_input.closure?.guest?.home === true), "
        "home_share_readwrite: (flow_input.closure?.guest?.home === true) }))"
    ),
    "kernel_boot": jx(
        '({kernel: flow_input.kernel?.mode === "build" ? results.build_kernel : results.resolve?.kernel, '
        'closure: flow_input.closure?.mode === "build" ? results.build_nix : results.resolve?.closure})'
    ),
}

modules = [
    {
        "id": "resolve",
        "summary": "Resolve reuse artifacts + host operator (f/qsu/resolve)",
        "value": {
            "type": "script",
            "path": "f/qsu/resolve",
            "input_transforms": {
                "kernel_index": jx(
                    'flow_input.kernel?.mode === "reuse" ? (flow_input.kernel?.kernel_pick || "") : ""'
                ),
                "qemu_index": jx(
                    'flow_input.qemu?.mode === "reuse" ? (flow_input.qemu?.qemu_pick || "") : ""'
                ),
                "closure_reuse": jx('flow_input.closure?.mode === "reuse"'),
                "vm_name": jx(
                    'flow_input.vm?.vm_target === "refresh" ? flow_input.vm?.refresh_vm : flow_input.vm?.vm_name'
                ),
            },
        },
    },
    {
        "id": "build_kernel",
        "summary": "Build the kernel (f/kernel/build)",
        "skip_if": {"expr": 'flow_input.kernel?.mode !== "build"'},
        "value": {"type": "flow", "path": "f/kernel/build", "input_transforms": kt},
    },
    {
        "id": "build_nix",
        "summary": "Build the NixOS closure (f/nix/build)",
        "skip_if": {"expr": 'flow_input.closure?.mode !== "build"'},
        "value": {"type": "flow", "path": "f/nix/build", "input_transforms": ct},
    },
    {
        "id": "build_qemu",
        "summary": "Build QEMU (f/qemu/build)",
        "skip_if": {"expr": 'flow_input.qemu?.mode !== "build"'},
        "value": {"type": "flow", "path": "f/qemu/build", "input_transforms": qt},
    },
    {
        "id": "boot",
        "summary": "Render + boot the VM (f/qsu/boot)",
        "value": {
            "type": "flow",
            "path": "f/qsu/boot",
            "input_transforms": boot_transforms,
        },
    },
]

bringup = {
    "summary": "Bring up a VM",
    "description": (
        "Build and/or reuse a kernel, a NixOS closure and QEMU, then boot a VM from them. "
        "Each artifact component has a mode: kernel/closure are build|reuse, QEMU is "
        "build|reuse|nixpkgs. `build` runs that build subflow; kernel/QEMU `reuse` picks a "
        "published artifact from this host's Nix-store index; closure `reuse` replays the "
        "init/initrd a refreshed VM recorded; QEMU `nixpkgs` boots the store QEMU. The final "
        "VM group is the target: a new VM, or refresh a deployed one in place (boot restarts "
        "it with the new render). Source and target are orthogonal: pick a new store kernel "
        "AND refresh an existing VM with it. Generated by scripts/gen-bringup.py from the "
        "subflows; do not hand-edit."
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
text = yaml.safe_dump(
    bringup, default_flow_style=False, sort_keys=False, width=4096, allow_unicode=True
)

if "--check" in sys.argv[1:]:
    try:
        on_disk = open(dest).read()
    except FileNotFoundError:
        on_disk = None
    if on_disk == text:
        print(f"OK: {dest} is up to date")
        sys.exit(0)
    diff = difflib.unified_diff(
        (on_disk or "").splitlines(keepends=True),
        text.splitlines(keepends=True),
        fromfile=f"{dest} (on disk)",
        tofile="gen-bringup.py (regenerated)",
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
