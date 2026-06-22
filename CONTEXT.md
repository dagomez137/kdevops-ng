# Context: workers, workbenches, worktrees and developers

The ubiquitous language for how build sources, artifacts and collaborators are
organized on disk and shared between humans and the Windmill engine. Glossary
only — no paths, no mechanisms. Terms still under grilling are marked
*(provisional)* and collected under "Flagged ambiguities".

"Workbench" is deliberately distinct from **Windmill workspace** (the `kdevops`
workspace-as-code): a workbench is a build sandbox on disk; a Windmill workspace
holds flow/script content. They may later be mapped, but they are different kinds
of thing.

## Actors

| Term          | Definition                                                                                       | Aliases to avoid |
| ------------- | ------------------------------------------------------------------------------------------------ | ---------------- |
| **Worker**    | A Windmill worker unit; the build executor.                                                       | builder          |
| **Developer** | A human authorized to drive the Windmill engine (CLI/UI) — create worktrees, refs, and the like. | operator, user   |

## Places

| Term                  | Definition                                                                                                                                                              | Aliases to avoid          |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| **Workbench**         | A movable, named sandbox where a worker and a developer collaborate on one topic. Default name `default`; many may exist. *Not* a Windmill workspace.                  | workspace, sandbox, bench |
| **System workbench**  | A movable *singleton* workbench, always present, user-scoped (no sudo in steady state). Owns the mirrors, bares, one-time setup, and health. Only one is active.        | service workbench         |
| **Project-namespace** | The per-project folder name within a workbench (`kernel`, `qemu-project`). **Never** equal to the project's canonical name.                                             | project                   |
| **Canonical name**    | A project's upstream source-directory name (`linux`, `qemu`).                                                                                                          | project name, source name |

## Source and artifacts

| Term               | Definition                                                                                                                                                                  | Aliases to avoid     |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- |
| **Worktree**       | A named git checkout of a project (default name `main`). A *developer worktree* is developer-owned; a *worker worktree* is worker-owned and synced to a ref. A worker never modifies a developer's worktree.                                   | tree, checkout       |
| **Build**          | The build directory paired with one worktree. A **child of the source worktree** by default (`<canonical>/build`, hidden via `.git/info/exclude`) so kbuild emits relative paths and artifacts relocate across hosts with no rewrite; an external/sibling location is allowed but forfeits cross-host LSP. One per worktree. | build dir, O=        |
| **Mirror**         | A *disposable* local cache of an upstream, force-refreshed on a timer. Pure ref/object source — it never holds worktrees or dev branches.                                  | cache, clone         |
| **Bare**           | The *durable* working repo: holds dev branches and all worktrees, borrows the Mirror's objects, and pulls the Mirror's refs into a remotes namespace. Never force-pruned. Per host. | remote, clone        |
| **Build identity** | A content hash of a build's inputs — config, toolchain (the Nix devShell's store hash), make flags, source commit — **baked into `kernelrelease` via `LOCALVERSION`** so the running kernel self-reports it. Same identity ⇒ same bytes. | build hash, release  |
| **Store**          | A content-addressed registry of built artifacts keyed by **Build identity** (echoing the Nix store). A host that lacks a needed identity *fetches* it from a peer (file-sync, or NFS when co-located) instead of rebuilding — fetch beats build. | destdir, artefactory, registry, releases |

## Relationships

- A **Workbench** contains one folder per **Project-namespace**.
- The pair (**Workbench**, **Project-namespace**) keys a VM's reusable artifacts in the **Store**.
- A **Project-namespace** holds many **Worktrees**; each **Worktree** is developer-controlled but worker-initialized.
- A **Build** is per-worktree (one per worktree, kept warm independently); the **Store** is project-level (one per namespace, shared across worktrees).
- Reproducibility is what makes *fetch* and *rebuild* interchangeable: two hosts building one **Build identity** produce the same bytes, so a host may fetch an identity from a peer or rebuild it locally and never repeat work either way.
- All **Worktrees** on a host hang off that host's **Bare**; the **Mirror** never holds worktrees.
- A **Worker** always builds in its own isolated **Worktree** (build as a child of the source); it never builds in or modifies a **Developer**'s worktree. A developer worktree receives worker output only by *materialize* (copy same-host, fetch cross-host).
- A **Developer** hands work to a **Worker** only by publishing a ref to the **Bare**; the worker consumes that ref into its own worktree. Same-host this is a commit (the developer worktree shares the **Bare**); cross-host it is a push/fetch. The worker builds committed refs, never working-tree state.
- There is no "reuse" toggle: a worker syncs *its own* worktree to the requested ref each build; developer worktrees are never touched, so the first-run chicken-and-egg cannot occur.
- A worker keeps one persistent, warm worktree per (worker, namespace) named `main`, synced to the target ref each build so rebuilds stay incremental (mode α's "no full rebuild"). It is disposable: explicit knobs *wipe the build* (clean rebuild) or *recreate the worktree* (fresh checkout). Developer worktrees are never wiped by the worker.
- Before every build the worker always `git fetch`es to get the target ref's latest tip — a branch/tag may be new or may have advanced, and fetching is the only way to guarantee latest. The single economy is scoping the fetch to the *one* remote that hosts the ref (the **Bare** for a dev branch, the **Mirror** or an upstream for an upstream ref), never all remotes.
- Refreshing the **Mirror** itself from upstream is the **System workbench**'s mirror timer (its cadence is user-set), separate from the per-build fetch above. Each Mirror is kept fresh by a `git-mirror@<repo>.{service,timer}` unit (instance = the mirror repo's base name, e.g. `linux`); **Bares** are un-timed (push targets, provisioned once).
- Cross-host: each host runs its own **System workbench**; a host reaches a peer's **Bare** through a per-host remote (`<hostname>/<project>` → that host's **Bare**, `ssh://` across hosts). Refs (build *inputs*) cross by git; **Store** entries (build *outputs*) cross on demand by file-sync. Build and boot may live on different hosts: build where it's powerful, boot where you are.
- Every **Worktree** knows three remote roles: its **Mirror** (fast local upstream refs), one or more **upstreams** (real URLs), and one **Bare** per participating host (the dev↔worker ref channel).
- A **Store** entry is keyed by **Build identity**; image and modules share one identity (`uname -r` resolves modules), so multiple VMs may boot an already-built identity without recompiling, and a fetched artifact is provably the one that was asked for.
- The **Store** is content-addressed: co-located hosts share one Store (NFS); a remote host fetches a missing identity from a named source over the same `ssh://` reachability that gates the **Bare** remote; a discovery index for many remote peers is deferred.
- The **System workbench** provides the **Mirror** and **Bare** that every **Workbench** on that host reuses (shared object store). Activating a new **System workbench** tears down the prior one's services and relaunches them at the new location.
- The **System workbench** runs **user-level by default** (`systemd --user` + lingering; sudo-less steady state); **system-level** (mirrors shared across all users) is an opt-in. All privileged provisioning — kvm group, vfio udev rules, and any system units — is folded into a one-time root setup; steady state is sudo-less in both modes.

## Decisions

Architectural decisions with lasting trade-offs are recorded in `docs/adr/`.
