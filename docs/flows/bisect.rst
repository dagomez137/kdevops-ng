.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

==========================
Bisect a kernel regression
==========================

The :src:`f/kernel/bisect` flow finds the first bad kernel commit for a
failing KUnit suite (the default payload; a host-side build payload follows
below) by driving :cmd:`git bisect` with real guest runs. Each
iteration builds the candidate commit, boots a dedicated guest with it, runs
the selected suites, and feeds the verdict back, until git names the first
bad commit. It composes the flows you already know: :src:`f/qsu/bringup`
does the build and boot (kernel artifacts are content-addressed, so a commit
built before is reused from the Nix store, not rebuilt), and
:src:`f/kunit/run` produces the verdict; the loop, the endpoints, and the
git state are the only new parts. See :doc:`guests` for how to watch a run
and reach the guest behind it.

Endpoints are verified before any bisecting. The bad ref runs standalone
first: if the picked suites pass alone, the run concludes
``not_reproducible_standalone`` and stops, because a failure that needs
other suites to run first cannot be bisected on its own; that conclusion is
itself the finding. The good ref runs next: if it fails, the run concludes
``good_endpoint_failed`` and the fix is to pick an older good. Only with a
failing bad and a passing good does the flow start the actual bisect.

The verdict for a candidate is the KUnit run's own folded verdict: good only
when every picked suite passed, bad otherwise, including a guest that dies
mid-suite (for a panic regression the dying guest is exactly the signal; the
next iteration's bringup replaces the guest). A kernel that fails to build
or boot is neither: the candidate is ``git bisect skip``\ ped. The flow
therefore bisects suite verdicts, not boot failures.

One state-machine step, :src:`f/kernel/bisect_step`, runs at the top of
every iteration and owns all of this. Its state survives on the host under
``$SYSTEM_DIR/bisect/<vm_name>/``: a ``--shared --no-checkout`` clone of the
durable Bare (objects are borrowed, no source tree is materialized; the
candidate lives in ``BISECT_HEAD``) plus ``state.json`` with the phase,
candidate, and per-iteration history. The previous candidate's verdict is
read from the freshest ``report.json`` the KUnit run wrote onto the guest's
share, never from in-flow results, so a failed build or a dead guest cannot
derail the decision. Rerunning the flow with the same inputs after a
conclusion, or with different endpoints or suites, resets the state and
starts fresh; an aborted run resumes where it stopped, and the state clone
also accepts a manual :cmd:`git bisect` for hand-driven follow-up.

The form asks for the two endpoints (from the Bare's live ref list, the same
picker as the build flows), the suites (pick the one failing suite alone for
the sharpest signal), a dedicated guest name (default ``bisect``; the guest
is replaced on every iteration, so keep it away from developer guests), an
iteration cap, and the per-suite timeout. ``report`` renders the iteration
table and, on completion, the first bad commit with its subject plus the
full ``git bisect log``; ``judge`` fails the job unless the run reached a
real conclusion, so an exhausted or untestable run is a red Windmill job.

A second payload, ``usertests_build``, bisects host-side build breaks in the
kernel's userspace test harnesses (the :doc:`usertests <usertests>` family
under ``tools/testing``). It never touches a guest: each iteration
:src:`f/kernel/check_usertests` sparse-checkouts the candidate in the state
clone and bare-makes the selected harnesses in the same devShell the real
suite build uses, so an iteration takes seconds instead of a
build-boot-run cycle. Because such harnesses can carry several independent
breaks at once, the ``error_re`` knob scopes the hunt to one failure
signature: a candidate whose build fails without matching it is ``git
bisect skip``\ ped as untestable (an older, unrelated break), which is what
lets each layer of a long-rotted harness be bisected in its own range.

Extending to another guest suite is mechanical: one more branch around the
run step and that suite's report location in :src:`f/kernel/bisect_step`.
