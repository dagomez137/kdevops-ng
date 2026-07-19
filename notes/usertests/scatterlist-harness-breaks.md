# tools/testing/scatterlist build rot: three bisect-proven breaks (2026-07-19)

Upstream-report candidates uncovered by the `f/kernel/bisect` flow's new
`usertests_build` payload. The scatterlist userspace harness
(`tools/testing/scatterlist`) hard-errored `build_usertests` during a
bringup at v7.2-rc3 and had already been excluded from the usertests
catalog as "broken at v7.2-rc1". The working theory at the time (header
gained folio/page_range_contiguous needs between rc1 and rc3) was wrong:
`include/linux/scatterlist.h` and the harness are byte-identical across
v7.1..v7.2-rc3. Endpoint probing showed the harness last built at v6.1
(December 2022) and has been unbuildable upstream ever since, through
three independent breaks layered on top of each other. Nothing builds it
in-tree (it is not wired into kselftest), which is why nobody noticed;
lore has no report of any of the three.

## Method: signature-scoped build bisects

Each layer was bisected with the `usertests_build` payload: per candidate,
a sparse checkout in the bisect state clone and a bare `make` of the
harness in the `build-usertests` devShell, seconds per iteration. The
`error_re` knob scopes a hunt to one failure signature; a candidate whose
build fails without the signature counts as good (the hunt asks when that
signature appeared, not whether the build is healthy), which is what lets
a newer layer be bisected inside a range every commit of which is already
broken by an older one. Four runs, each converging in exactly 14 feed
steps plus the two endpoint verifications:

| range | signature (`error_re`) | first bad commit |
| --- | --- | --- |
| v6.1..v6.2 | (any build failure) | `1567b49d1a408` |
| v6.4..v6.5 | `incomplete definition of type 'struct folio'` | `bdadc6d831560` |
| v6.17..v6.18 | `page_range_contiguous` | `80e7bb74d4ff2` |
| v6.17..v6.18 | `incomplete type 'struct page'` | `80e7bb74d4ff2` |

## Break 1: v6.2-rc1, `zone_device_pages_have_same_pgmap()`

`1567b49d1a408` ("lib/scatterlist: add check when merging zone device
pages", Logan Gunthorpe, 2022-10-21) made `lib/scatterlist.c` call
`zone_device_pages_have_same_pgmap()`. The harness sed-copies that file
verbatim (`scatterlist.c: ../../../lib/scatterlist.c` in its Makefile) and
its `linux/mm.h` shim never gained the helper:

    scatterlist.c:417:7: error: call to undeclared function
        'zone_device_pages_have_same_pgmap'

## Break 2: v6.5-rc1, `sg_set_folio()` dereferences an incomplete folio

`bdadc6d831560` ("scatterlist: add sg_set_folio()", Matthew Wilcox,
2023-06-21) added a static inline to `include/linux/scatterlist.h` (which
the harness copies verbatim as its `linux/scatterlist.h`) that does
`sg_assign_page(sg, &folio->page)`. No harness header defines
`struct folio`:

    ./linux/scatterlist.h:186:27: error: incomplete definition of type
        'struct folio'

## Break 3: v6.18-rc1, `page_range_contiguous` + `struct page` arithmetic

`80e7bb74d4ff2` ("scatterlist: disallow non-contigous page ranges in a
single SG entry", David Hildenbrand, 2025-09-01) added
`VM_WARN_ON_ONCE(!page_range_contiguous(...))` to `sg_set_page()` (the
shim has neither symbol) and converted `sg_page_iter_page()` from
`nth_page(sg_page(piter->sg), piter->sg_pgoffset)` to direct
`sg_page(piter->sg) + piter->sg_pgoffset`, pointer arithmetic on the
`struct page` that the userspace build only forward-declares
(`tools/include/linux/types.h:16`). Both v6.18 signatures bisect to this
one commit. The irony: the same series' `84efbefa26df3` ("mm: remove
nth_page()") dutifully deleted the now-unused `nth_page` macro from the
harness's own `tools/testing/scatterlist/linux/mm.h`, the last commit
ever to touch the harness, keeping the shim in step with a change whose
sibling had already made the harness unbuildable.

## Reproducer

Any of the tags after v6.1, with a current compiler (the devShell's
clang; implicit function declarations are hard errors under C99-and-later
defaults, and the incomplete-type errors are unconditional):

    make --directory=tools/testing/scatterlist

## Fix candidates

Teach the shim headers what the copied sources now need: a
`zone_device_pages_have_same_pgmap()` stub returning true, a minimal
`struct folio` wrapping `struct page`, a `page_range_contiguous()` stub
returning true, `VM_WARN_ON_ONCE` mapped to the existing `WARN_ON_ONCE`,
and a complete `struct page` where the userspace build today only has the
forward declaration. The deeper fix is wiring the harness into something
that builds it (kselftest or a bot target); three silent breaks in six
releases show that a test harness nothing compiles is already dead.
Until it is fixed upstream, `scatterlist` stays out of the usertests
harness catalog default.

## What the bisect payload proved

The four runs were the first use of the `usertests_build` payload
(`f/kernel/check_usertests` + the `error_re` scoping in
`f/kernel/bisect_step`), driven by the very step modules the flow
composes. Verify-endpoints-first paid off again: the initial
rc1-versus-rc3 framing died at `verify_good` cost, minutes, and the
corrected endpoints came from cheap tag probes before any bisect ran.
