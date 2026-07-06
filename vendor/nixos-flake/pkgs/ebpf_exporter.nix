# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Prometheus exporter for custom eBPF metrics, from Cloudflare.
# https://github.com/cloudflare/ebpf_exporter
#
# Two artifacts from one source: the Go binary (CGo against the
# system libbpf, BUILD_LIBBPF=0 semantics) and the upstream example
# programs, each a clang -target bpf object beside its YAML config,
# installed under share/ebpf_exporter/examples. The objects are
# CO-RE: compiled once against the repository's vendored vmlinux.h
# and relocated at load time against the running kernel's BTF
# (/sys/kernel/btf/vmlinux), so they are kernel-independent. At
# runtime the exporter takes --config.dir and --config.names to pick
# which programs to load.
{
  lib,
  buildGoModule,
  fetchFromGitHub,
  pkg-config,
  clang,
  libbpf,
  elfutils,
  zlib,
  stdenv,
}:
buildGoModule rec {
  pname = "ebpf_exporter";
  version = "2.5.1";

  src = fetchFromGitHub {
    owner = "cloudflare";
    repo = "ebpf_exporter";
    rev = "v${version}";
    hash = "sha256-zIevVZ4ldPj/4OvQFo+Nv/g//xNZEppO9ccB6y65rZA=";
  };

  vendorHash = "sha256-ZwKXIIoV4yEyjSpGjVDr91/CQmVuF9zc0IHkJYraE9o=";

  subPackages = [ "cmd/ebpf_exporter" ];

  nativeBuildInputs = [
    pkg-config
    clang
  ];
  buildInputs = [
    libbpf
    elfutils
    zlib
  ];

  ldflags = [
    "-X github.com/prometheus/common/version.Version=${version}"
  ];

  # libbpfgo leaves the libbpf link line to the caller (the upstream
  # Makefile passes it the same way).
  env.CGO_LDFLAGS = "-lbpf";

  # The cc-wrapper's hardening flags (stack protector,
  # zero-call-used-regs) do not exist for -target bpf and clang
  # rejects them under -Werror; its --gcc-toolchain is likewise
  # unused there, so that warning is silenced via the Makefile's
  # CFLAGS hook below.
  hardeningDisable = [ "all" ];

  # The examples' own Makefile computes the clang BPF system include
  # workaround; BUILD_LIBBPF=0 keeps it off the vendored libbpf and
  # LIBBPF_CFLAGS points it at the system headers instead.
  postBuild = ''
    make --directory=examples \
      BUILD_LIBBPF=0 \
      CC=clang \
      CFLAGS=-Wno-unused-command-line-argument \
      LIBBPF_CFLAGS=-I${lib.getDev libbpf}/include \
      build
  '';

  postInstall = ''
    mkdir --parents $out/share/ebpf_exporter/examples
    cp examples/*.yaml examples/*.bpf.o $out/share/ebpf_exporter/examples/
  '';

  meta = {
    description = "Prometheus exporter for custom eBPF metrics";
    homepage = "https://github.com/cloudflare/ebpf_exporter";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "ebpf_exporter";
  };
}
