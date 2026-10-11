# deploy

The Windmill instance (localhost:8000, one DB, one `wmill.yaml`) is deployed
from Nix: [nix/](nix/) builds the server from the pinned fork and runs it under
`systemd --user`. Deploy it, then `wmill sync push`.
