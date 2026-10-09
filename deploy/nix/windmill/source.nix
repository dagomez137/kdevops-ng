# SPDX-License-Identifier: copyleft-next-0.3.1
# The single pin for the dagomez137 Windmill fork (branch integration/fixes).
# The server and the workspace CLI are built from one tree, so `wmill` is always
# generated against the `openapi.yaml` of the backend it talks to. Bump this file
# alone to move both.
{ fetchFromGitHub }:
{
  version = "1.824.1";

  src = fetchFromGitHub {
    owner = "dagomez137";
    repo = "windmill";
    rev = "4350d4dfbb9490c8620067bd76cb9cfc1f03f290";
    hash = "sha256-MKFuvAsF+hx0WpFk8VYpKejsEfdvkzZEECBxv5ACZjs=";
  };
}
