# Plan: generate a step's commands apart from running them

Working note, 2026-10-10. Status: proposal, nothing built. It serves the
"Commands a developer can run" principle (docs/concepts/principles.rst): every
step's job log should be a recipe a developer can replay by hand, and the
"Equivalent command" text should come from the same data as the run.

## Where we are (at this note's commit)

- Runners in `f/common/devshell.py` (`DevShell`, `Git`, `Nix`, `Systemd`,
  `run_logged`) and `f/common/remote.py` (`RemoteSystemd.ssh`) log `+ <argv>`
  and execute in one call. Generation and execution are not separable, so a
  plan cannot be printed without running it.
- 143 step scripts (`f/**/*.script.yaml`); 111 of the `f/**/*.py` carry a
  hand-written "Equivalent ..." docstring,
  which drifts: `f/fstests/start.py` documents `systemctl --host` but runs
  `ssh <vm> systemctl`; `f/fstests/wipe.py` documents three commands but runs
  a ten-clause `bash -c` loop.
- Seven sites hand a Python-assembled string to `sh -c` or `bash -c`:
  `f/common/remote.py` (the `command -v systemctl` probe),
  `f/kernel/build_flags.py` (a `printf` through bash), `f/kunit/discover.py`,
  `f/blktests/discover.py` (twice), `f/fstests/wipe.py` and
  `f/blktests/wipe.py` (the device loops). The runner prints each whole, so a
  developer can replay it, but nothing checks the string against the docstring.
- What runs is not always what is printed: the devShell env (`PATH`,
  `NO_COLOR`, `CCACHE_CONFIGPATH`), `cwd=` and `env=` are never logged
  (`f/kernel/install.py` runs `make install` under a temp `HOME` holding a
  Python-written `installkernel` hook; `f/kernel/configure_fragments.py` passes
  `LLVM=1` through `env=`).
- Files are written from Python in 30 files (`write_unit`, `_atomic_write`,
  `shutil.copyfile`); the log says `wrote <path>` but not the bytes.
- `RemoteSystemd.ssh` prints `+ ssh <vm> ...`, which works only with
  `Include $SYSTEM_DIR/ssh/config` in `~/.ssh/config`, and the real argv uses
  the devShell's `systemd-ssh-proxy` while `f/qsu/boot.py` writes the alias with
  `/usr/lib/systemd/systemd-ssh-proxy`.
- 38 hand-written `print("+ ...")` lines in 26 files, some duplicating what
  the runner logs.

## References

cijoe (`~/src/cijoe`): a task is a list of steps whose `run:` lines become
one command each (`core/resources.py:354-396`); scripts often build a list of
commands, then run it (`linux/scripts/build_kdebs.py:60-77`); every `run()`
leaves `cmd_NN.output` and `cmd_NN.state` (cmd, cwd, err, timing) under the
step's output directory (`core/command.py`), rendered into an HTML report.
Borrow the per-command record and "list first, run after". Do not borrow shell
strings with `shell=True`, env injected silently, paramiko instead of `ssh`, or
`put`/`get` calls with no command-line equivalent.

go-task (`~/src/task`): a compile phase resolves every command before
execution (`variables.go:79`); the executor prints the compiled command and,
under `--dry`, returns before running it (`task.go:395-401`), so the printed
and executed text cannot differ; `--summary` lists commands from the same data
(`internal/summary/summary.go`). Borrow the compile-then-execute split and the
shared print path. Do not borrow shell strings, a YAML DSL (Windmill is the
DAG), or commands run while compiling (`sh:` variables).

## Proposal

A step becomes `plan(inputs, facts) -> Plan`, pure, plus a thin `main()` that
gathers facts, executes the plan and builds the result. One renderer makes
the job-log lines, a copy-pasteable script and the docstring "Equivalent"
block from the same objects.

```python
@dataclass(frozen=True)
class Local: pass                               # host argv (run_logged today)
@dataclass(frozen=True)
class Shell: name: str = "build-kernel"         # nix develop <vendor>#<name> --command
@dataclass(frozen=True)
class Ssh: vm: str; shell: Shell = Shell("systemd")

@dataclass(frozen=True)
class Cmd:
    argv: tuple[str, ...]
    via: Local | Shell | Ssh = Shell()
    cwd: str | None = None                      # rendered: cd X &&
    env: Mapping[str, str] = {}                 # rendered: env K=V ...
    check: bool = True
    capture: bool = False                       # stdout to the probe's caller
    stdin: str | None = None                    # rendered: quoted heredoc

@dataclass(frozen=True)
class WriteFile:                                # the one non-Cmd action
    path: str; content: str; mode: int = 0o644  # rendered: install -D --compare ... <<'EOF'

Plan = list[Cmd | WriteFile]
def render(plan, *, symbolic=False) -> str      # shell script, env preamble once
def execute(plan, *, dry_run=False) -> dict     # logs render(one cmd), runs it
```

- `render` and `execute` share one `full_argv()`, so the log cannot lie.
- Values from earlier flow steps are plain inputs. A value from a command in
  the same step splits the step into a probe plan and a main plan, with the
  decision printed as data; no `Ref` captures, so a script never depends on
  its own output.
- Jinja rendering stays in Python; its output is a `WriteFile`'s content, so
  the script carries the bytes and the job log carries the path and size.
- Polling loops (`wait`, boot readiness) stay in Python around one `Cmd`.
- Each run returns `commands.sh` (`set -euo pipefail`) in the step result;
  `explain` returns that script without executing.
- Each step module exposes `EXAMPLE` inputs; `scripts/gen-equivalent` renders
  the docstring block between markers, and a test fails on drift.
- Plan builders are pure, so fixture tests assert on the exact `Plan`.

What not to do: no Ansible-style modules (`ensure_dir`,
`unit(state=started)`) that hide the commands; no `sh -c` or f-string shell; no
YAML DSL; no commands while building a plan; no second dispatcher beside
`devshell` (it becomes the executor). Everything still runs in a pinned
devShell, as the user, in the worker sandbox, through systemd.

## Migration, one commit each

1. `f/common/plan.py` types and `render`, with tests; no callers.
2. `execute`; reimplement the runners on it, output unchanged except that
   `cwd` and `env` are now printed.
3. Print the devShell env preamble once per job (fixes the install and
   configure_fragments logs without touching them).
4. `RemoteSystemd.ssh` builds `Cmd(via=Ssh(vm))`; align the proxy path that
   `f/qsu/boot.py` writes.
5. `WriteFile` executor; move `write_unit` and `_atomic_write` onto it.
6. `commands.sh` in the step result and the generic `explain` script.
7. `gen-equivalent` and the drift test, opt-in per step.
8. Convert steps one per commit, worst first: `fstests/start`,
   `fstests/prepare`, `kernel/install`, `kernel/configure_fragments`,
   `fstests/wipe` and `blktests/wipe` (drop `bash -c`), then the qsu renders.
9. Drop the hand-written `+` prints as steps migrate; make the drift test
   mandatory.

## Decisions

1. `commands.sh` goes in the step result, not the worker sandbox: a result
   lives with the job and shows in the UI, while the sandbox is reset by the
   next run.
2. The log assumes `Include $SYSTEM_DIR/ssh/config` in `~/.ssh/config`, the
   one-time line requirements.rst lists, so `Ssh` renders `ssh <vm> ...`.
3. `NO_COLOR` and `CCACHE_CONFIGPATH` move into the devShell's `shellHook`,
   so the preamble is the shell alone.
4. No `Ref` captures: a step that needs a command's output splits into a
   probe plan and a main plan, with the decision printed as data.
5. No per-step dry-run input: one generic `explain` script takes a step path
   and its inputs and returns `commands.sh`, so step schemas stay unchanged.
6. Wipe runs one `Cmd` per device, with the counting in Python; no
   guest-side script.
