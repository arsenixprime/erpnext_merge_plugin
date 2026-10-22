# Development Environment Setup

Reproducible bring-up of the Frappe/ERPNext dev environments used to
develop `merge_tool`, based on the official `frappe_docker` development
workflow (docker-compose, no VSCode devcontainer required). The main
sections below describe the **v15** bench (`version-15` branch); see
"v16 bench" for `develop` / `version-16`.

## Layout on the host

```
<workspace>/                          # scratch dir (not the app repo)
├── db_backups/                       # safety dumps taken before any DB drop
└── frappe_docker/                    # clone of github.com/frappe/frappe_docker
    ├── .devcontainer/                # copied from devcontainer-example, then edited (see below)
    └── development/                  # bind-mounted into the frappe container at /workspace/development
        ├── apps-v15.json             # pins erpnext to version-15 for installer.py
        └── frappe-bench/
            └── apps/merge_tool/      # ← THE APP REPO (this repo)
```

Only `apps/merge_tool` is a deliverable git repo. The bench, sites, and
frappe_docker clone are disposable scaffolding and must never be committed.

## Versions (pinned)

| Component | Version | Why |
|---|---|---|
| frappe / erpnext | branch `version-15` (15.111.0 at setup time) | matches production |
| Python (bench venv) | 3.12.12 via pyenv in the bench image | v15-compatible; preinstalled in `frappe/bench` image |
| Node | v20.20.2 via nvm | matches official v15 images; image default (24) is untested with v15 |
| MariaDB | 10.6 | frappe v15 warns above 10.8 ("not yet tested") |
| bench CLI | 5.29.1 (image-provided) | |

## Bring-up from scratch

```bash
# 1. Clone frappe_docker
git clone https://github.com/frappe/frappe_docker
cd frappe_docker

# 2. Dev container config
cp -R devcontainer-example .devcontainer
cp -R development/vscode-example development/.vscode
```

Edit `.devcontainer/docker-compose.yml`:

- `mariadb` service: change image to `docker.io/mariadb:10.6`
  (and rename the volume if an incompatible 11.x volume already exists).
- `frappe` service: if DNS is broken inside containers (see Quirks), add:
  ```yaml
  dns:
    - 1.1.1.1
    - 8.8.8.8
  ```

```bash
# 3. Pin ERPNext to version-15 for the installer
cat > development/apps-v15.json <<'EOF'
[
  {
    "url": "https://github.com/frappe/erpnext.git",
    "branch": "version-15"
  }
]
EOF

# 4. Start containers (mariadb, redis-cache, redis-queue, frappe)
docker compose -f .devcontainer/docker-compose.yml up -d

# 5. One-time container prep: node 20 + yarn
#    (the image ships node 24 with yarn; v15 needs node 20, which has no yarn)
docker exec devcontainer-frappe-1 bash -ic "nvm install 20 && nvm alias default 20"
docker exec devcontainer-frappe-1 bash -ic "nvm use v20.20.2 && npm install -g yarn"

# 6. Build the bench + site (frappe version-15, erpnext, site dev.localhost,
#    developer_mode on, admin password "admin", db root password "123")
docker exec -w /workspace/development devcontainer-frappe-1 \
  bash -ic "python installer.py -j apps-v15.json -t version-15 -p 3.12.12 -n v20.20.2 -s dev.localhost"

# 7. Allow running tests on the site
docker exec -w /workspace/development/frappe-bench devcontainer-frappe-1 \
  bash -ic "bench --site dev.localhost set-config allow_tests true"

# 8. Get the merge_tool app (fresh setup: clone from the Bitbucket repo)
docker exec -w /workspace/development/frappe-bench devcontainer-frappe-1 \
  bash -ic "bench get-app <BITBUCKET_REPO_URL>"
docker exec -w /workspace/development/frappe-bench devcontainer-frappe-1 \
  bash -ic "bench --site dev.localhost install-app merge_tool"

# 9. Serve
docker exec -d -w /workspace/development/frappe-bench devcontainer-frappe-1 \
  bash -ic "nvm use v20.20.2 >/dev/null 2>&1; bench start > /tmp/bench-start.log 2>&1"
```

Open <http://dev.localhost:8000> — log in as `Administrator` / `admin`.
(`*.localhost` resolves to 127.0.0.1 on most systems; add an `/etc/hosts`
entry if it doesn't.)

## v16 bench (develop / version-16)

A second, independent bench for v16, verified on this host. The
`frappe/bench` image defaults (Python 3.14.2, Node 24.13.0, yarn) are exactly
what v16 needs, so no nvm/pyenv prep is required, and MariaDB 11.8 from the
stock compose file is supported. A separate compose project (`-p mtv16`)
keeps its containers and volumes apart from the v15 set.

```bash
# in a fresh clone of frappe_docker
cp -R devcontainer-example .devcontainer
cp -R development/vscode-example development/.vscode
```

Edit `.devcontainer/docker-compose.yml`, `frappe` service:

```yaml
    dns: [1.1.1.1, 8.8.8.8]          # quirk 1
    ports:
      - 127.0.0.1:8016:8000          # site at http://dev.localhost:8016
      - 127.0.0.1:9016:9000
```

```bash
cat > development/apps-v16.json <<'JSON'
[{"url": "https://github.com/frappe/erpnext.git", "branch": "version-16"}]
JSON

docker compose -p mtv16 -f .devcontainer/docker-compose.yml up -d
docker exec -w /workspace/development mtv16-frappe-1 \
  bash -ic "python installer.py -j apps-v16.json -t version-16 -p 3.14.2 -n v24.13.0 -s dev.localhost"
```

Then mount the app repo into the bench instead of cloning it, so tests run
against the working tree. Only after the installer has finished (quirk 4),
add to the `frappe` service's volumes and recreate it:

```yaml
      - /path/to/erpnext_merge_plugin:/workspace/development/frappe-bench/apps/merge_tool
```

```bash
docker compose -p mtv16 -f .devcontainer/docker-compose.yml up -d frappe
docker exec -w /workspace/development/frappe-bench mtv16-frappe-1 bash -ic '
  uv pip install --python env/bin/python -e apps/merge_tool &&
  printf "frappe\nerpnext\nmerge_tool\n" > sites/apps.txt &&
  bench build --app merge_tool &&
  bench --site dev.localhost install-app merge_tool &&
  bench --site dev.localhost set-config allow_tests true &&
  bench --site dev.localhost run-tests --app merge_tool'
```

Check out the repo on `develop`/`version-16` while using this bench.

For browser use, the v16 desk redirects every page to the setup wizard
until setup is marked complete (quirk 5 still applies: seed with
`merge_tool.dev_seed.run` instead of running the wizard):

```bash
docker exec -w /workspace/development/frappe-bench mtv16-frappe-1 bash -ic \
  "echo 'frappe.db.set_value(\"Installed Application\", {\"app_name\": (\"in\", [\"frappe\", \"erpnext\"])}, \"is_setup_complete\", 1); frappe.db.commit()' \
   | bench --site dev.localhost console"
docker exec -d -w /workspace/development/frappe-bench mtv16-frappe-1 bash -ic \
  "bench start > /tmp/bench-start.log 2>&1"
```

Realtime (socket.io) is not reachable from the host with these port mappings
(the client connects to port 9000); the merge UI does not use it.


```bash
# Shell inside the dev container
docker exec -it -w /workspace/development/frappe-bench devcontainer-frappe-1 bash

# Serve (inside container, or detached as in step 9)
bench start

# Console
bench --site dev.localhost console

# Tests for this app
bench --site dev.localhost run-tests --app merge_tool

# After changing Python code while bench start is running: web worker reloads
# automatically in developer mode. After changing JS/CSS: bench watch handles it.
# After changing DocType JSON: bench --site dev.localhost migrate
```

The bench lives on the host at `frappe_docker/development/frappe-bench/`, so
you can edit `apps/merge_tool` with any host editor; the container sees changes
immediately through the bind mount.

## Quirks encountered on this host (Arch Linux, Docker 29)

1. **Container DNS is broken with the default config.** Docker's embedded DNS
   forwards to the host's resolver (172.31.0.1 here), which drops queries from
   the docker bridge. Raw IP connectivity works; only name resolution fails.
   Fix: `dns: [1.1.1.1, 8.8.8.8]` on the `frappe` service in
   `.devcontainer/docker-compose.yml`.
2. **`frappe/bench:latest` image defaults are too new for v15** (Python 3.14,
   Node 24). Python 3.12.12 is preinstalled in pyenv and works; Node 20 must be
   `nvm install`ed, and yarn must be reinstalled under it (step 5). Every
   `bench` invocation that builds assets should run after `nvm use v20.20.2`.
3. **The compose file's default MariaDB (11.8) is untested with v15** — frappe
   prints a warning at site install. Pinned to 10.6 instead. If you previously
   ran with 11.x, the data volume is incompatible with 10.6; use a fresh volume
   name (`mariadb-data-v15`).
4. **`installer.py` skips bench init if `frappe-bench/` exists** — even a
   partial, broken one. If a run fails midway, `rm -rf` the bench dir (and drop
   the half-created site DB: `DROP DATABASE <hash>;` as mariadb root, password
   `123`) before retrying. Take a `mariadb-dump` first if in doubt.
5. **The site skips the ERPNext setup wizard**, so wizard-created records are
   missing: no Company, Fiscal Year, Warehouse Types, item/customer/supplier
   group roots, UOMs, or default Address Template. Without a Fiscal Year
   covering today, ERPNext party dashboards (e.g. opening any Supplier form)
   throw `FiscalYearError: Date … is not in any active Fiscal Year`.
   `bench --site dev.localhost execute merge_tool.dev_seed.run` creates
   everything needed for the manual walkthrough, fiscal year included; the
   test suite creates its own fixtures.

## Credentials (dev-only, throwaway)

- Site `dev.localhost`: `Administrator` / `admin`
- MariaDB root: `123`

These are the frappe_docker development defaults; the environment listens only
on localhost. Never reuse them anywhere real.
