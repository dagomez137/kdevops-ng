# SPDX-License-Identifier: copyleft-next-0.3.1
# The single pin for the dagomez137 Windmill fork (branch integration/fixes).
# The server and the workspace CLI are built from one tree, so `wmill` is always
# generated against the `openapi.yaml` of the backend it talks to. Bump this file
# alone to move both.
{ fetchFromGitHub }:
{
  version = "1.799.0";

  src = fetchFromGitHub {
    owner = "dagomez137";
    repo = "windmill";
    rev = "32173d769400b2042a58f4a4a7cc6da4ecce31fc";
    hash = "sha256-sHJYiyMWrRQrLqJ1gr0Wse4zf0fcmr9p9jZuHVOFyNQ=";
  };
}
