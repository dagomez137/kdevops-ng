# SPDX-License-Identifier: copyleft-next-0.3.1
#
# blktests: test framework for the Linux kernel block layer and
# storage stack. Installs the check runner with its tests, shared
# helpers, and the compiled src/ helper programs under
# $out/blktests; check runs from that directory (its tests/ and
# common/ paths are relative) and writes only where --output and
# --config point.
#
# liburing gates the io_uring helpers and recent kernel headers gate
# miniublk; both are feature-detected by src/Makefile.
#
# The carried patch runs each test in a transient systemd scope with
# an optional RuntimeMaxSec deadline (TEST_TIMEOUT/TEST_TIMEOUTS), so
# a hung test is observable and killable from outside. patches
# applies to any src, so a source override keeps it.
#
# Source: https://github.com/linux-blktests/blktests
{
  lib,
  stdenv,
  fetchFromGitHub,
  liburing,
}:

stdenv.mkDerivation {
  pname = "blktests";
  version = "0-unstable-2026-08-02";

  src = fetchFromGitHub {
    owner = "linux-blktests";
    repo = "blktests";
    rev = "fc6e3ffbd58c58c1eec213552479030a396e3476";
    hash = "sha256-Gy5mr8xdXcc+CrzbRyp48cKSwhfCLjL7gKUpldDVxik=";
  };

  patches = [ ./blktests-runtime-max-sec.patch ];

  buildInputs = [ liburing ];

  makeFlags = [ "prefix=${placeholder "out"}" ];

  # Upstream `make install` flattens the sg/ helpers into src/, but the
  # tests resolve them as src/sg/<name>, so scsi/001 and scsi/002 would
  # skip. Restore the source layout.
  postInstall = ''
    mkdir --parents $out/blktests/src/sg
    mv $out/blktests/src/dxfer-from-dev \
      $out/blktests/src/syzkaller1 \
      $out/blktests/src/sg/
  '';

  enableParallelBuilding = true;

  meta = {
    description = "Linux kernel block layer test suite";
    homepage = "https://github.com/linux-blktests/blktests";
    license = lib.licenses.gpl3Plus;
    platforms = lib.platforms.linux;
    maintainers = [ ];
  };
}
