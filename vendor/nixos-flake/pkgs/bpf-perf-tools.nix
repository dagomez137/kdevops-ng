# SPDX-License-Identifier: copyleft-next-0.3.1
#
# bpf-perf-tools: the bpftrace tools published with BPF Performance
# Tools (Brendan Gregg, Addison-Wesley 2019).
#
# 126 short programs, one per book section, grouped by chapter:
# CPUs, memory, filesystems, disks, networking, security, languages,
# applications, the kernel, containers and hypervisors. They are the
# reference implementations the book explains, so they read as
# teaching material first: each one traces the smallest set of events
# that answers its question, and several are the ancestor of a tool
# that later grew a C rewrite in BCC.
#
# The scripts are unmaintained since 2020 (the last commit here) and
# several are older than the kernel interfaces they use, which is the
# point of shipping them verbatim: a tool that stops matching the
# kernel is evidence about the interface it chose. `nvmelatency.bt`
# is one, it kprobes `nvme_setup_cmd` and `nvme_complete_rq` and
# reads `request->rq_disk`; that field was removed in v5.17
# (f3fa33acca9f), and since v5.16 (4f5022453acd) the interrupt
# handler batches completions into `nvme_complete_batch()`, which
# ends the requests without calling `nvme_complete_rq()`.
#
# The shebangs name /usr/local/bin/bpftrace, the path the book's
# build instructions produce; patchShebangs points them at the
# bpftrace in the closure instead.
#
# Source: https://github.com/brendangregg/bpf-perf-tools-book
{
  lib,
  stdenv,
  fetchFromGitHub,
  bpftrace,
}:

stdenv.mkDerivation {
  pname = "bpf-perf-tools";
  version = "0-unstable-2020-04-20";

  src = fetchFromGitHub {
    owner = "brendangregg";
    repo = "bpf-perf-tools-book";
    rev = "09f8a3a101fdc23b0e51c33158ea3a1782aea427";
    hash = "sha256-DMChfbeKfkXcpDgIlqeKyiraiexez/90QX6ZAAR7fG8=";
  };

  # bpftrace is here for patchShebangs to resolve, and to be on the
  # path of anyone who installs this.
  buildInputs = [ bpftrace ];

  dontBuild = true;

  installPhase = ''
    runHook preInstall

    # The chapter layout is how the book indexes them, so it is kept;
    # bin/ carries the flat set, since a tool is named by the book
    # under one name only.
    mkdir --parents $out/bin $out/share/bpf-perf-tools
    cp --recursive originals/* $out/share/bpf-perf-tools/
    for tool in $out/share/bpf-perf-tools/*/*.bt; do
      ln --symbolic --relative "$tool" $out/bin/
    done

    chmod --recursive u+w $out/share/bpf-perf-tools
    patchShebangs $out/share/bpf-perf-tools

    runHook postInstall
  '';

  meta = {
    description = "The bpftrace tools from the BPF Performance Tools book";
    homepage = "https://github.com/brendangregg/bpf-perf-tools-book";
    license = lib.licenses.asl20;
    platforms = lib.platforms.linux;
    maintainers = [ ];
  };
}
