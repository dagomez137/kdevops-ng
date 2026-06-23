# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared library for the f/qsu/* steps (QEMU-in-systemd VM orchestration).
# Not a runnable step — imported by the render/boot steps as f.qsu.common.
#
# Two concerns:
#   * build the qsu template vars dict from QEMU-keyword flow inputs + the
#     kernel/closure build manifests (shares, nvme drives, kernel args, ports);
#   * render the vendored qsu .j2 templates with jinja2 (qsu authors them for
#     both ansible/jinja2 and minijinja, so trim_blocks is the only knob).
# The host `systemd --user` manager is driven through f.common.devshell.Systemd;
# /nix/store and qemu/virtiofsd binary resolution lives in f.qsu.binaries.
import hashlib
import os
from pathlib import Path

import jinja2
import yaml

from f.common.devshell import DevShell, Systemd

from f.qsu.binaries import _workers, resolve_qemu_binary, resolve_virtiofsd_binary

# Superset of every virtiofsd share tag the steps render an env file for
# (ports the qsu role's qsu_canonical_share_tags). boot.py restarts only the
# sockets whose tag is in this set so a re-render never touches sockets serving
# VMs owned by other trees on the same host; destroy.py enumerates which
# per-share artefacts to remove. Keep in sync with _shares() below.
CANONICAL_SHARE_TAGS = [
    "store", "modules", "data-configs", "data-results", "kdevops-fstests",
    "fstests", "home", "controller-share",
]


def qsu_dir(workers: Path | None = None) -> Path:
    """The vendored qemu-system-units tree (host-visible under workers/shared)."""
    return (workers or _workers()) / "shared/qemu-system-units"


def resolve_vm_name(fi: dict) -> str:
    """Derive the VM name: auto (flow-job-id slug) or the operator's `vm_name`.

    `auto_vm_name` on + `WM_ROOT_FLOW_JOB_ID` set = `vm-<first job-id segment>`, a
    filesystem/systemd-instance-safe slug that is unique per flow run and identical
    across the flow's steps. Off (or no job id) = the given `vm_name`.
    """
    if fi.get("auto_vm_name", True):
        jid = os.environ.get("WM_ROOT_FLOW_JOB_ID")
        if jid:
            return "vm-" + jid.split("-")[0]
    return fi["vm_name"]


# --- jinja2 rendering of the vendored qsu templates ----------------------------
def render(template: str, qsu_vars: dict, workers: Path | None = None) -> str:
    """Render one qsu template. trim_blocks matches `minijinja-cli --trim-blocks`."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(qsu_dir(workers) / "templates")),
        trim_blocks=True,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    return env.get_template(template).render(**qsu_vars)


def write_unit(path: Path, content: str) -> None:
    # Skip identical rewrites so the mtime never moves: a same-bytes rewrite
    # still bumps mtime, which daemon-reload reads as a fragment change and
    # which knocks a running socket-activated unit out of service.
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text() == content:
            print(f"unchanged {path} ({len(content.encode())}B)", flush=True)
            return
    except (FileNotFoundError, UnicodeDecodeError):
        pass
    path.write_text(content)
    print(f"wrote {path} ({len(content.encode())}B)", flush=True)


# --- systemd config + state layout (the host user manager's search path) -------
def systemd_config(home: Path | None = None) -> Path:
    base = home or Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "systemd"


def state_dir(vm_name: str) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state"))
    return base / "qemu-system" / vm_name


_VM_UNIT_PREFIX = "qemu-system@"
_VM_UNIT_SUFFIX = ".service"
# A loaded-unit glob fed to `systemctl --user list-units` (local and over ssh).
_VM_UNITS_LISTING = ("list-units", "--type=service", "--no-legend",
                     f"{_VM_UNIT_PREFIX}*{_VM_UNIT_SUFFIX}")


def _running_vms(out: str) -> set[str]:
    """VM names from `systemctl --user list-units 'qemu-system@*.service'` output.

    qsu boots each VM as a `qemu-system@<vm>.service` user unit, so the running set
    is those loaded units. `machinectl` is not usable here: it has no `--user` scope
    and the units register with the per-user machine registry over Varlink, not the
    system `machined` the CLI talks to — so the user manager's own unit list is the
    reliable source. Scans every token so a leading status bullet never hides a unit.
    """
    vms = set()
    for line in out.splitlines():
        for tok in line.split():
            if tok.startswith(_VM_UNIT_PREFIX) and tok.endswith(_VM_UNIT_SUFFIX):
                vms.add(tok[len(_VM_UNIT_PREFIX):-len(_VM_UNIT_SUFFIX)])
                break
    return vms


def _peer_hosts(workers: Path) -> list[str]:
    """Registered peer ssh-host aliases (one per line in `system/peers`, written by
    f/workspace/fetch). Missing/empty file means no peers, so discovery stays local."""
    f = workers / "system/peers"
    if not f.is_file():
        return []
    return [h.strip() for h in f.read_text().splitlines() if h.strip()]


def vm_options(filter_text: str = "") -> list[dict]:
    """Existing QEMU/systemd VMs as `[{label, value}]` for a `dynselect-` dropdown.

    Local VMs are the running user units (`systemctl --user list-units
    'qemu-system@*.service'`) plus any with a rendered `<vm>.env` still on disk
    (stopped but not torn down). Each registered peer (`system/peers`) is then swept
    best-effort over ssh (the same `systemctl --user list-units`) so the dropdown
    spans every workbench host; an unreachable peer drops out silently rather than
    failing the local list. Peer VMs are labelled `<vm> (<peer>)`. The lifecycle
    scripts wrap this as their `list_vms` dynselect entrypoint; it runs on a
    `vm`/`hetzie-vm` worker (host manager + ssh to peers).
    """
    workers = Path(os.environ["WORKERS_DIR"])
    ft = filter_text.lower()
    local = Systemd(workers).systemctl(*_VM_UNITS_LISTING, capture=True, check=False) or ""
    rendered = {p.stem for p in (systemd_config() / "qemu-system").glob("*.env")}
    options = [{"label": vm, "value": vm}
               for vm in sorted(_running_vms(local) | rendered) if ft in vm.lower()]
    seen = {o["value"] for o in options}
    for peer in _peer_hosts(workers):
        # -F names the config explicitly: the `#systemd` dev shell does not read
        # ~/.ssh/config, so the peer Host/IdentityFile aliases (system/ssh) must be
        # pointed at directly. BatchMode never prompts; ConnectTimeout bounds a dead
        # peer; check=False so a down peer yields "" and is skipped, leaving the local
        # dropdown intact. The glob is single-quoted so the peer's shell passes it to
        # systemctl unexpanded.
        remote = DevShell(workers, "systemd").capture(
            "ssh", "-F", str(workers / "system/ssh/config"),
            "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", peer,
            f"systemctl --user list-units --type=service --no-legend "
            f"'{_VM_UNIT_PREFIX}*{_VM_UNIT_SUFFIX}'", check=False) or ""
        for vm in sorted(_running_vms(remote)):
            if vm not in seen and ft in vm.lower():
                options.append({"label": f"{vm} ({peer})", "value": vm})
                seen.add(vm)
    return options


# --- vars composition (ports the qsu role's render-per-vm.yml glue) ------------
def _shares(fi: dict, modules_dir: str | None) -> list[dict]:
    # An explicit `shares` list is a FULL replacement — it skips every predefined
    # share below (store, modules, fstests, home). Callers that override must include
    # whatever they still need (e.g. the fstests guest would have to re-add the
    # /var/lib/xfstests share itself, or its xfstests@.service fails to start).
    if fi.get("shares"):
        return fi["shares"]
    shares = [{"tag": "store", "dir": "/nix/store", "mount": "/nix/store"}]
    if modules_dir:
        shares.append({"tag": "modules", "dir": modules_dir, "mount": "/lib/modules"})
    # Predefined `fstests` share (like store/modules): a writable per-VM dir the
    # fstests test suite reads its config from and writes results to. Added when the
    # guest runs the fstests suite (`fi["fstests"]`, derived from the closure's
    # test_suites; render_config declares the matching /var/lib/xfstests mount). Host
    # dir follows f/fstests/common.share_dir's convention
    # ($WORKERS_DIR/shared/fstests/<vm>). Predefined, so the operator's free-form
    # controller-share stays separate and untouched.
    if fi.get("fstests"):
        # The closure declares the /var/lib/xfstests mount whenever the fstests suite is
        # on, so a missing/crafted vm_name must fail loudly here, not silently drop the
        # host share and leave the guest with an unservable mount.
        vm = fi.get("vm_name") or ""
        root = _workers() / "shared/fstests"
        fdir = (root / vm).resolve()
        if not vm or root.resolve() not in fdir.parents:
            raise ValueError(f"fstests share needs a valid vm_name, got {vm!r}")
        shares.append({"tag": "fstests", "dir": str(fdir), "mount": "/var/lib/xfstests"})
    # Predefined `home` share: the operator's host home shared into the guest at the
    # SAME absolute path (so host/guest paths match). Toggled by `fi["home_share"]`;
    # read-only unless `home_share_readwrite`. Independent of the free-form
    # controller-share, which can still expose a different directory.
    if fi.get("home_share"):
        home = str(Path.home())
        s = {"tag": "home", "dir": home, "mount": home}
        if not fi.get("home_share_readwrite"):
            s["options"] = ["ro"]
        shares.append(s)
    if fi.get("controller_share"):
        s = {
            "tag": fi.get("controller_share_tag", "controller-share"),
            "dir": fi.get("controller_share_dir", str(Path.home())),
            "mount": fi.get("controller_share_guest_mount", fi.get("controller_share_dir", str(Path.home()))),
        }
        if not fi.get("controller_share_readwrite"):
            s["options"] = ["ro"]
        shares.append(s)
    return shares


def nvme_drives(fi: dict) -> list[dict]:
    """The per-VM nvme drive dicts (file/format/serial + BlockConf knobs).

    Public so nvme/create creates exactly the qcow2 files vm.env references.
    """
    return _nvme_drives(fi)


# Per-drive NVMe knob tables: param name (fi key) -> QEMU template key (keeping the
# `.`/`-` spelling the .j2 reads). CTRL -> -device nvme, NS -> -device nvme-ns,
# BLOCKCONF -> whichever device owns the drive backend.
NVME_CTRL_KNOBS = (
    ("mdts", "mdts"),
    ("atomic_awun", "atomic.awun"),
    ("atomic_awupf", "atomic.awupf"),
)
NVME_NS_KNOBS = (
    ("atomic_nawun", "atomic.nawun"),
    ("atomic_nawupf", "atomic.nawupf"),
    ("atomic_nabsn", "atomic.nabsn"),
    ("atomic_nabspf", "atomic.nabspf"),
    ("atomic_nabo", "atomic.nabo"),
)
NVME_BLOCKCONF_KNOBS = (
    ("logical_block_size", "logical_block_size"),
    ("physical_block_size", "physical_block_size"),
    ("min_io_size", "min_io_size"),
    ("opt_io_size", "opt_io_size"),
    ("discard_granularity", "discard_granularity"),
    ("write_cache", "write-cache"),
)


def _drive_pick(raw: str, i: int) -> str:
    """Resolve a per-drive knob for drive `i` from a single value or comma-list.

    A bare value (no comma) applies to every drive; a comma-list assigns by index
    ("4096,512" -> drive0=4096, drive1=512, beyond-list/empty parts -> ""). Each
    part is whitespace-trimmed; the caller omits the key when the result is empty.
    """
    raw = "" if raw is None else str(raw)
    parts = raw.split(",")
    value = parts[0] if len(parts) == 1 else (parts[i] if i < len(parts) else "")
    return value.strip()


def _bucket(fi: dict, knobs, i: int) -> dict:
    out = {}
    for param, tkey in knobs:
        v = _drive_pick(fi.get(param), i)
        if v:
            out[tkey] = v
    return out


def _nvme_drives(fi: dict) -> list[dict]:
    if fi.get("nvme_drives"):
        return fi["nvme_drives"]
    count = int(fi.get("nvme_drive_count", 0) or 0)
    drives = []
    for i in range(count):
        base = {"file": f"nvme{i}.qcow2", "format": "qcow2", "serial": f"kdevops{i}"}
        ctrl = _bucket(fi, NVME_CTRL_KNOBS, i)
        ns = _bucket(fi, NVME_NS_KNOBS, i)
        blockconf = _bucket(fi, NVME_BLOCKCONF_KNOBS, i)
        # atomic.dn is a controller boolean, not a comma-list.
        if fi.get("atomic_dn"):
            ctrl["atomic.dn"] = True
        # CMB (controller memory buffer, BAR 2): a per-drive size in MiB; 0 or an empty
        # comma-list part means no CMB on that drive ("64,0" -> drive 0 only), unlike the
        # generic knobs where 0 is a meaningful value. legacy-cmb (v1.3 register scheme) is a
        # controller boolean that only applies where a CMB exists.
        cmb = _drive_pick(fi.get("cmb_size_mb"), i)
        if cmb and cmb != "0":
            ctrl["cmb_size_mb"] = cmb
            if fi.get("legacy_cmb"):
                ctrl["legacy-cmb"] = True
        # PMR is a controller feature (pmrdev= on -device nvme), so it lives in ctrl and
        # lands on the controller in both simple and explicit mode. A non-empty per-drive
        # pmr_size enables it; share/pmem are global toggles. We fail fast on a size QEMU
        # would reject (power-of-2, >= 16 bytes) rather than let the VM die deep in boot.
        pmr_size = _drive_pick(fi.get("pmr_size"), i)
        if pmr_size:
            try:
                size = int(pmr_size)
            except ValueError:
                raise ValueError(f"nvme drive {i} pmr size {pmr_size!r} is not an integer (bytes)")
            # 0 (or an empty comma-list part) means no PMR on this drive — lets a comma-list
            # enable PMR on a subset ("16777216,0" -> drive 0 only).
            if size:
                # QEMU's NVMe code wants pow2 and >= 16 bytes, but the memory backend
                # separately rejects a size below one host page, which is the binding floor
                # in practice — a sub-page pow2 size fails at -object creation, deep in boot.
                # Mirror the real floor (the worker shares the host kernel, so SC_PAGESIZE is
                # the host page that governs the backing-file mmap).
                page = os.sysconf("SC_PAGESIZE")
                if size & (size - 1) != 0 or size < page:
                    raise ValueError(
                        f"nvme drive {i} pmr size {size} must be a power of 2 and at least one "
                        f"host page ({page} bytes); QEMU rejects sub-page or non-pow2 sizes"
                    )
                pmr = {"size": size}
                # The qsu nvme_pmr_object macro defaults share=on, so only override when off.
                if not fi.get("pmr_share", True):
                    pmr["share"] = False
                # pmem only has an effect with share on: QEMU silently maps MAP_PRIVATE and
                # ignores pmem on a non-shared backend (no error, no warning), so refuse the
                # meaningless combination rather than render a no-op flag.
                if fi.get("pmr_pmem"):
                    if not fi.get("pmr_share", True):
                        raise ValueError(
                            f"nvme drive {i} pmr_pmem requires pmr_share — QEMU silently ignores "
                            "pmem on a non-shared (MAP_PRIVATE) PMR backend"
                        )
                    pmr["pmem"] = True
                ctrl["pmr"] = pmr
        # Per-namespace atomics are only valid on -device nvme-ns, forcing explicit mode.
        if ns:
            drives.append({
                "serial": base["serial"], **ctrl,
                "namespaces": [{"file": base["file"], "format": base["format"],
                                **blockconf, **ns}],
            })
        else:
            drives.append({**base, **ctrl, **blockconf})
    return drives


def _kernel(fi: dict, kernel: dict | None, closure: dict | None) -> dict | None:
    image = fi.get("kernel_image") or (kernel or {}).get("vmlinuz") or (kernel or {}).get("bzImage")
    if not image:
        return None
    init = fi.get("closure_init") or (closure or {}).get("init")
    append = fi.get("kernel_append") or (
        f"root=tmpfs console=ttyS0,115200 console=hvc0 init={init}" if init else None
    )
    k = {"image": image}
    if append:
        k["append"] = append
    initrd = fi.get("kernel_initrd") or (closure or {}).get("initrd") or (kernel or {}).get("initrd")
    if initrd:
        k["initrd"] = initrd
    return k


# Range the per-VM port/cid offset wraps to. Wide enough that a stable hash of the
# (unique) vm_name rarely collides for the handful of VMs booted at once; ssh ports stay
# in 10022..~20000 and vsock cids in 100..~10000.
PORT_OFFSET_MODULO = 9973


def _port_offset(fi: dict) -> int:
    """Per-VM offset added to `ssh_port_base`/`vsock_cid_base`.

    An explicit non-zero `vm_index` wins (deterministic manual multi-VM allocation).
    Otherwise derive a stable offset from the (unique) `vm_name` so concurrent auto-named
    VMs (`vm-<jobid>`, all `vm_index` 0) do not collide on the host port or the host-global
    vsock CID. sha256 keeps it deterministic across runs — Python's built-in `hash()` is
    salted per process and would not. Residual collisions are rare, not impossible: set an
    explicit `vm_index` or port/cid when you need a guaranteed-distinct value.
    """
    idx = int(fi.get("vm_index", 0) or 0)
    if idx:
        return idx
    digest = hashlib.sha256((fi.get("vm_name") or "").encode()).hexdigest()
    return int(digest[:8], 16) % PORT_OFFSET_MODULO


def build_vars(fi: dict, kernel: dict | None = None, closure: dict | None = None,
               workers: Path | None = None) -> dict:
    """Compose the qsu template vars dict from QEMU-keyword inputs + manifests.

    `fi` is the flow input (QEMU keyword names). `kernel`/`closure` are the
    f/kernel/build and f/nix/build result manifests. Returns the dict every
    qsu .j2 consumes; keys it omits stay undefined (templates no-op on them).
    """
    offset = _port_offset(fi)
    v: dict = {
        "vm_name": fi["vm_name"],
        "service_scope": fi.get("service_scope", "user"),
        "qemu_binary": resolve_qemu_binary(fi, workers),
        "virtiofsd_binary": resolve_virtiofsd_binary(fi, workers),
        "cpu": fi.get("cpu", "host"),
        "accel": fi.get("accel", "kvm"),
        "ram": int(fi.get("ram", 4096)),
        "cpus": int(fi.get("cpus", 4)),
        "machine_type": fi.get("machine_type", "q35"),
        "share_transport": fi.get("share_transport", "virtiofs"),
        "ssh_port": int(fi["ssh_port"]) if fi.get("ssh_port") else int(fi.get("ssh_port_base", 10022)) + offset,
        "vsock_cid": int(fi["vsock_cid"]) if fi.get("vsock_cid") else int(fi.get("vsock_cid_base", 100)) + offset,
    }
    # A kernel image and its /lib/modules are a unit: an explicit kernel_image takes the
    # explicit modules_dir (a version-mismatched modules tree from a different build would
    # break the guest), else both come from the manifest. render validates they're set
    # together, so explicit-image-without-modules never reaches here.
    if fi.get("kernel_image"):
        modules_dir = fi.get("modules_dir")
    else:
        modules_dir = (kernel or {}).get("modules_dir") or (kernel or {}).get("modules")
    shares = _shares(fi, modules_dir)
    if shares:
        v["shares"] = shares
    drives = _nvme_drives(fi)
    if drives:
        v["nvme"] = {"drives": drives}
    k = _kernel(fi, kernel, closure)
    if k:
        v["kernel"] = k
    if fi.get("iommu"):
        v["iommu"] = fi["iommu"]
    return v


def emit_vars_yaml(vm_name: str, qsu_vars: dict) -> str:
    """Optional debug snapshot mirroring the ansible debug-vars.yaml.j2 file."""
    dest = qsu_dir() / "vars" / f"{vm_name}.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(qsu_vars, default_flow_style=False, sort_keys=True))
    print(f"wrote {dest} (vars snapshot)", flush=True)
    return str(dest)


def main():
    """Library module imported by the f/qsu/* steps; not a runnable step."""
    return "f/qsu/common: qsu vars/render/systemd helpers"
