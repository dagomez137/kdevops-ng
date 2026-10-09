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
    rev = "07cf9d3ec237cbb6bfdae8069b054b5f65f7c409";
    hash = "sha256-73YyWLlUOb6aiejyQMK1YYcZD0TjAJ/0lBQTe3HNOIM=";
  };
}
