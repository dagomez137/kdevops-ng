<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->
# Contributing to kdevops

kdevops is developed the same way as the Linux kernel: changes are sent as
emailed patches to the mailing list, reviewed in the open, and carried with
`Signed-off-by` trailers. You use the same tools you already use for the kernel —
`git`, [`b4`](https://b4.docs.kernel.org/), and `get_maintainer.pl`.

- **List:** `kdevops@lists.linux.dev`
- **Archive:** <https://lore.kernel.org/kdevops/>

## One-time setup

- Install `b4` (`pipx install b4`, your distro's package, or run it from a
  checkout of <https://git.kernel.org/pub/scm/utils/b4/b4.git>).
- Configure `git send-email` with an identity that can send to the list. The
  per-project `.b4-config` already points `b4` at the kdevops list and lore.

## Submitting a change

1. Branch off the default branch and make your change.
2. Run the style checks: `make style` (whitespace, EOF newlines, generated-file
   drift, and the HEAD commit-message trailers).
3. Commit with a sign-off: `git commit -s`. Follow the kernel-style commit rules
   in `CLAUDE.md` — a `subsystem: summary` subject in the imperative mood within
   75 characters, the body wrapped at 75, one logical change per commit, and the
   `Signed-off-by` trailer your `-s` adds (this is your DCO certification, see
   `DCO`). AI-assisted work also carries `Generated-by:` immediately above the
   `Signed-off-by`.
4. Find who to Cc:

   ```
   make maintainers FILE=f/fstests/report.py
   # or directly:
   ./scripts/get_maintainer.pl --no-tree -f f/fstests/report.py
   ```

5. Prepare and send the series with `b4`:

   ```
   b4 prep -n my-topic-branch       # start a tracked series off your commits
   b4 prep --auto-to-cc             # fill To:/Cc: from MAINTAINERS via get_maintainer.pl
   b4 prep --edit-cover             # write the cover letter (for a multi-patch series)
   b4 send                          # send to the list (use -d first for a dry run)
   ```

   A single patch can also go out with plain `git send-email`, but `b4` is the
   first-class path: it tracks revisions, threads the series, and collects
   trailers for you.

## Reviewing and applying (maintainers)

```
b4 shazam <message-id>     # fetch the series and apply it to the current branch
b4 am <message-id>         # fetch as an mbox without applying
b4 trailers -u             # collect Reviewed-by/Tested-by from list replies
b4 ty -a                   # send thank-you notes for applied series
```

`b4 shazam` is already wired into kdevops' own build flows (the **b4 Series**
input applies a lore series on top of a kernel/QEMU checkout before building).
