# SPDX-License-Identifier: copyleft-next-0.3.1
# The single pin for the dagomez137 Windmill fork (branch integration/fixes).
# The server and the workspace CLI are built from one tree, so `wmill` is always
# generated against the `openapi.yaml` of the backend it talks to. Bump this file
# alone to move both.
{ fetchFromGitHub }:
{
  version = "1.785.0";

  src = fetchFromGitHub {
    owner = "dagomez137";
    repo = "windmill";
    rev = "f1065e71a23719d22a55a6a2ae0de93b0360662b";
    hash = "sha256-FmvPn8Og6053iFStExgZMHZaxKDl/os1CPM0uwrb86U=";
  };
}
