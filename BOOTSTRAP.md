# kdevops-ng: bootstrap

Rootless-podman Windmill (localhost only) + workspace-as-code in git.

## 1. Host deps (distro packages)
```bash
sudo apt install --yes podman crun uidmap passt dbus-user-session aardvark-dns netavark npm
sudo npm install --global windmill-cli  # wmill CLI: no distro package
```

## 2. Instance up (backend: podman; see deploy/README.md for distro/nix)
```bash
./deploy/podman/install.sh              # build + start; UI on 127.0.0.1:8000
# WORKERS=4 ./deploy/podman/install.sh  # N general workers = N concurrent builds
```

## 3. Connect workspace (first time)
```bash
TOKEN=$(curl --silent localhost:8000/api/auth/login --header 'content-type: application/json' \
  --data '{"email":"admin@windmill.dev","password":"changeme"}')
curl --silent --request POST localhost:8000/api/workspaces/create \
  --header "Authorization: Bearer $TOKEN" --header 'content-type: application/json' \
  --data '{"id":"kdevops","name":"kdevops"}'
wmill workspace add kdevops kdevops http://localhost:8000/ --token "$TOKEN"
wmill init          # generate AI context (AGENTS.cli.md, skills, tsconfig), git-ignored
```

## 4. Daily loop
```bash
wmill sync pull        # instance → files (this repo)
# edit f/… then:
wmill sync push        # files → instance
git add --all && git commit --signoff   # see CLAUDE.md for the commit-message format
```

## UI
```bash
ssh -L 8000:localhost:8000 dagomez@hz-debian   # → http://localhost:8000  (admin@windmill.dev / changeme)
```

## Teardown (pristine)
```bash
./deploy/podman/teardown.sh
```
