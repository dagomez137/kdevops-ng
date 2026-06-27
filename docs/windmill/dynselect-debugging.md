# Debugging Windmill `dynselect` reuse pickers (findings)

A handoff and postmortem for the multi-day effort to make the `f/qsu/bringup`
reuse pickers render. The artifacts (kernel, QEMU) are chosen from a `dynselect`
dropdown nested inside a `type:object` component group. Three bugs stacked on top
of each other, each hiding the next, while the backend was correct the whole
time. This records the symptoms, the root causes, the fixes, and the techniques
that actually localised the problem, so the next dynselect does not cost the same
days.

## The shape of the feature

A bringup component (Kernel, QEMU) is one `type:object` group whose first field is
a `mode` enum (`build` | `reuse` | ...). When `mode === "reuse"` a `showExpr`
reveals a picker field with `format: dynselect-list_<x>_index`. The flow's
`x-windmill-dyn-select-code` defines the helper (`list_kernel_index`, ...), which
reads this host's Nix-store index and returns `[{label, value}]`. The generated
flow is `f/qsu/bringup.flow`; never hand-edit it, edit `scripts/gen-bringup.py`.

## Symptoms, in the order they appeared

1. The reuse field rendered as a raw JSON input, not a dropdown.
2. After fixing that, the dropdown showed `Loading...` forever, then nothing, and
   the sibling `mode` selector became unchangeable ("frozen").
3. Browser console: `https://svelte.dev/e/effect_update_depth_exceeded` (Svelte 5
   infinite effect loop).
4. Network: the `run/dynamic_select` request fired only once (not repeatedly).

## Root causes (three layers)

**L1. A dynselect field must be `type: object`.** Windmill binds the dropdown
widget to a `type:object` field; a `type:string` field with a `dynselect-` format
silently falls back to a plain input. The working pickers (`iommu`,
`reuse_from_vm`) were always `type:object`. Fix: declare the pickers `type:object`
(the selected value still resolves to the picked string for consumers).

**L2. Nested-dynselect retrigger loop.** The fork's nested-dynselect support
(`9743f2c8af`) exposed the whole run-form's args to a nested helper and, in doing
so, tied `DynamicInput`'s change-detection and debounce to `mergedArgs`
(`{...rootArgs, ...otherArgs}`). For a nested field `rootArgs` is the entire form,
so the helper job was re-issued and cancelled on every form change. This is the
"Loading forever, network keeps firing" failure. Fix: drive change-detection and
the debounce off own-level `otherArgs` while still sending `mergedArgs`/`_rootArgs`
in the eval payload, so nested helpers still see root fields. Once fixed, the
network fires once.

**L3. `effect_update_depth_exceeded`: a cross-component `value` ping-pong.** With a
picker declared `default: ""`, two Svelte effects fight over the shared bindable
value:

- `ArgInput` re-applies the default: when `value` is `undefined`/`null` it writes
  `value = ""`.
- `DynamicInput`'s value-reconciliation effect nulls an unknown selection: since
  `""` is never a member of the resolved options (the index names are non-empty),
  it writes `value = undefined`.

So `"" -> undefined -> "" -> undefined ...` with no fixpoint, until Svelte 5 aborts
with `effect_update_depth_exceeded` and the form's reactivity dies (empty dropdown,
frozen siblings). This only bites a `showExpr`-gated `type:object` dynselect whose
`""` default is not a valid option. The always-visible `iommu` is fine because its
empty selection is a valid member of its own option set.

## The fixes

**kdevops-ng (no Windmill rebuild needed):** in `scripts/gen-bringup.py` the three
pickers are `type:object` and carry NO `default`. With no default the value starts
`undefined`, the reconciliation effect (gated on a defined value) never nulls, and
L3 cannot start. Push the flow with `wmill sync push` and the dropdown works on the
running Windmill.

**Windmill fork (`deploy/nix/windmill/package.nix` rev), for durability:** one
commit, `fix(frontend): let nested dynselect helpers work`, in
`frontend/src/lib/components/DynamicInput.svelte`:

- retrigger/debounce on `otherArgs`, not `mergedArgs` (L2);
- guard the eval on a non-empty `entrypoint`;
- do not null `value` when it equals `""` (L3), so an empty default is safe.

After this guard, a `default: ""` on a dynselect no longer loops, so the kdevops
no-default rule becomes belt-and-suspenders rather than mandatory.

## How to test and verify (the techniques that worked)

**Prove the backend in isolation.** The dynselect helper runs as a job; call it
directly and the frontend is removed from the equation:

    curl -s -X POST "http://localhost:8002/api/w/<ws>/jobs/run/dynamic_select" \
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -d '{"entrypoint_function":"list_kernel_index",
           "args":{"_ENTRYPOINT_OVERRIDE":"list_kernel_index","filterText":""},
           "runnable_ref":{"source":"deployed","path":"f/qsu/bringup","runnable_kind":"flow"}}'

It returns a job uuid; read the result with `wmill job result <uuid>`. The entry
point comes from `args._ENTRYPOINT_OVERRIDE`, NOT the request field, for the flow
path; omit it and the job calls `main` and fails with "no attribute main" (a red
herring that cost time). Test BOTH `runnable_ref` shapes: the flow run page uses
`{source: deployed, runnable_kind: flow}`, the flow editor preview uses
`{source: inline, code, lang}`. Both returned the items here, which proved the bug
was purely client-side.

**Confirm the deployed build actually carries the fix.** The frontend is embedded
in the Rust binary, so grep the separately-built web-ui, not the binary:

    nix build .#windmill.web-ui --no-link --print-out-paths   # in deploy/nix
    grep -rl "DYNSELECT_ROOT_ARGS_KEY\|_rootArgs" <that path>

and confirm the running server serves it:

    curl -s http://localhost:8002/_app/immutable/chunks/<chunk>.js | grep _rootArgs

**The decisive frontend signal is in the browser, not the server.** When the
backend is proven good, ask for exactly two things: the Console error text and
whether `run/dynamic_select` (Network tab) fires once or repeatedly. "Fires once +
`effect_update_depth_exceeded`" means a client reactive loop, not the network
retrigger; "fires repeatedly" means the retrigger loop. That single observation
distinguishes L2 from L3 in seconds.

**Validate a `.svelte` change** with the Svelte skill's autofixer before
committing: `npx @sveltejs/mcp svelte-autofixer <file>`; `issues: []` is the pass.

## Gotchas worth keeping

- A dynselect dropdown MUST be `type: object`. `type: string` renders a plain
  input with no error.
- A dynselect `default: ""` whose `""` is not a valid option loops
  `effect_update_depth_exceeded`. Prefer no default on dynselect pickers.
- The deployed Windmill lives at `~/.local/state/windmill/workbench` (the systemd
  `%S` state dir), not the repo's `./workbench/`. The latter is a stale dev copy.
- The Store index moved (`f113ba4`) from `WORKERS_DIR/shared/store-index` to
  `store_index_dir()` = `SYSTEM_DIR/store-index`; the code change and the on-disk
  data migration are separate steps, and a dropdown reading the new path sees an
  empty index until the data is re-rooted there.
- Dynselect helper jobs run on a default worker (which carries the build-area env),
  not the native worker; env was checked and was never the cause here, but it is
  the first thing to rule out if a helper returns `[]`.
- The custom Windmill is pinned by rev+hash in `deploy/nix/windmill/package.nix`; a
  fork change needs a force-push, a rev/hash bump, and a rebuild to go live. The
  kdevops-side fix (no default) needed none of that.
