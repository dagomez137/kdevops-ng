# SPDX-License-Identifier: copyleft-next-0.3.1
# The single pin for the dagomez137 Windmill fork (branch integration/fixes).
# The server and the workspace CLI are built from one tree, so `wmill` is always
# generated against the `openapi.yaml` of the backend it talks to. Bump this file
# alone to move both.
{ fetchFromGitHub }:
{
  version = "1.800.0";

  src = fetchFromGitHub {
    owner = "dagomez137";
    repo = "windmill";
    rev = "d2aa6c8f7bc2372c05c6d29f1fd97478901fe5d7";
    hash = "sha256-gCZdnJj9GzyypDfhgv4VUKzT/4ZrvNrUnB+N5f2UMSU=";
  };
}
