# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Let xfsprogs build from a developer's own source (an `xfsprogs-src` override),
# not only the release tarball.
#
# xfsprogs keeps its custom install-sh -- the one that understands the
# `-T so_dot_version` keyword the shared-library install uses -- at
# include/install-sh, while configure.ac's AC_CONFIG_AUX_DIR([.]) makes autoreconf
# drop a *standard* install-sh at the repo root. A release tarball ships the custom
# root install-sh, so autoreconf (run without --force) leaves it alone; a git
# checkout has none, so autoreconf's standard install-sh wins and `make install`
# dies with "so_dot_version does not exist". Restore the custom install-sh after
# configure and patch its shebang. Harmless for the tarball (same file), decisive
# for a git src.
final: prev: {
  xfsprogs = prev.xfsprogs.overrideAttrs (prevAttrs: {
    postConfigure = (prevAttrs.postConfigure or "") + ''
      cp -f include/install-sh install-sh
      patchShebangs install-sh include/install-sh
    '';
  });
}
