# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Build toolchain package sets, shared by the controller NixOS module and the
# build devShells so a control node and an any-host shell agree on versions.
{ pkgs }:
{
  # Kernel + Kconfig toolchain a kernel tree's make / `make *config` needs
  # beyond stdenv.
  kernel = with pkgs; [
    ncurses
    pkg-config
    gcc
    gnumake
    bison
    flex
    bc
    perl
    openssl
    elfutils
    pahole
    binutils # readelf/objdump/nm/strings for inspecting build artifacts
    patchelf
    cpio
    kmod
    zstd
    rsync
    hostname
  ];

  # Controller orchestration / provisioning tools.
  orchestration = with pkgs; [
    ansible
    git
    qemu-utils
    virtiofsd
    python3
    python3Packages.pyyaml
    python3Packages.jinja2
  ];

  # Multi-toolchain build matrix (gcc AND clang AND rust AND sparse),
  # selected per build via make flags (LLVM=1, C=1) / CONFIG_RUST.
  matrixExtras = with pkgs; [
    clang
    lld
    llvm # llvm-objcopy/ar/nm/... so an LLVM=1 build is self-contained
    rustc
    rust-bindgen
    rustfmt
    sparse
  ];

  # The checkers the kernel's pre-submission documentation names beyond what
  # a build needs (Documentation/process/submit-checklist.rst,
  # Documentation/dev-tools/, Documentation/rust/).
  kernelChecks = with pkgs; [
    clippy # make CLIPPY=1
    coccinelle # make coccicheck
    # `spatch` compiles the script:ocaml rules against its own interfaces,
    # so the compiler must be the OCaml Nixpkgs builds Coccinelle with.
    ocaml-ng.ocamlPackages_4_14.ocaml
    ocaml-ng.ocamlPackages_4_14.findlib
    libabigail # scripts/check-uapi.sh runs abidiff
    # make htmldocs; Documentation/sphinx/requirements.txt
    (python3.withPackages (
      ps: with ps; [
        alabaster
        sphinx
        pyyaml
      ]
    ))
  ];
}
