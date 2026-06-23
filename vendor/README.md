# vendor/

Pinned copies of upstream projects that define what kdevops-ng builds and boots
(the Nix devShells, the kernel `.config` fragments, the VM systemd unit
templates). Tracked in git, carried with every clone. Why they live here and not
as mirrors or submodules: `docs/adr/0006` and `docs/adr/0007`.

## How it works

Each subdir is a [git-subrepo](https://github.com/ingydotnet/git-subrepo): a copy
of an upstream repo whose provenance lives in `<subdir>/.gitrepo` (remote,
branch, pinned commit, pull method). Updating is a deliberate `git subrepo pull`,
never automatic. Local patches are ordinary commits on top of the pin; a `pull`
reconciles them with upstream, and a patch that has landed upstream drops out by
itself.

| Subrepo | Upstream | Method |
| --- | --- | --- |
| `nixos-flake` | `github.com/linux-kdevops/nixos-flake` | `rebase` (carries our downstream patches) |
| `qemu-system-units` | `github.com/linux-kdevops/qemu-system-units` | `merge` (clean mirror) |
| `linux-config-fragments` | `github.com/dagomez137/linux-config-fragments` | `merge` (we lead it; push first) |

## Roles

- **Read-only users**: install nothing. `git clone` gets every subrepo already in
  place.
- **Collaborators** who pull or push a subrepo: install git-subrepo once with
  `source /path/to/git-subrepo/.rc`. (Needs git >= 2.23.)

## Commands (collaborators)

```
git subrepo status                  # list subrepos and their pins
git subrepo pull vendor/<name>      # bump from upstream (reconciles local patches)
git subrepo push vendor/<name>      # send local commits upstream
git format-patch <pin>..HEAD -- vendor/<name>   # or mail patches the kernel way
```

After bumping `nixos-flake`, re-pin its consumer lock (Windmill does this via
`f/nix/lock_config` with `update=true`):

```
nix flake update --flake "path:$config_dir" nixos-flake
```
