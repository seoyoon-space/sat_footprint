# Deploying to a test server (e.g. 192.168.0.82:8080)

**Goal:** run `hk_api` as its own standalone, network-reachable API - not just embedded/proxied
inside `attitude-viewer`'s Flask process (`HK_API_BASE_URL` -> `/api/hk/*` today). Same pattern
`attitude-viewer` itself already uses in production: it's reachable at
`http://192.168.1.61:8080/sat_footprint/viewer?satellite=O1B` - a dedicated path added behind an
existing shared `:8080` entry point, not its own port exposed to the internet. This doc sets up
`hk_api` the same way, behind `192.168.0.82:8080`, at whatever new path you pick (`/sat-api/`
below - rename freely, just keep it consistent across the reverse-proxy config and any caller).

## Checklist (Option A / Docker, end to end)

Run all of this **on the target server itself** over SSH - it needs that server's own
filesystem/Docker/credentials, so it can't be done remotely from a dev machine.

1. `ssh <user>@192.168.0.82`
2. `git clone https://github.com/seoyoon-space/sat_footprint.git && cd sat_footprint/hk_api`
3. `HOST_PORT=8081 bash deploy/deploy.sh` - first run only creates `.env`/`config/satellites.toml`
   from the `*.example` templates and exits (see step 4)
4. Fill in real values in `.env` (`MYSQL_*`, `API_KEY`, `CORS_ALLOWED_ORIGINS`) and
   `config/satellites.toml` if per-satellite DB profiles are needed - **never commit either file**
5. `HOST_PORT=8081 bash deploy/deploy.sh` again - builds the image, starts the container
   (`--restart unless-stopped`), and runs a local health check itself
6. Confirm locally: `curl -sf http://127.0.0.1:8081/health` -> `{"status":"ok"}`
7. **Find what currently serves `192.168.0.82:8080`** (the EP server) - e.g. `sudo ss -tlnp |
   grep :8080` or `ps aux | grep -iE 'nginx|caddy|iis'` - this is server-specific and not
   something this repo can know in advance; ask whoever manages the EP server if unclear
8. Add the reverse-proxy route for that server (nginx example and other-proxy notes below), then
   reload/restart it (e.g. `sudo nginx -t && sudo systemctl reload nginx`)
9. Confirm externally, from another machine: `curl http://192.168.0.82:8080/sat-api/health`
10. On the `attitude-viewer` side, point `HK_API_BASE_URL` at `http://192.168.0.82:8080/sat-api`
    and restart that service - it now calls `hk_api` as a standalone API instead of a locally-run
    process (see "After either option" below)

Steps 3-6 are Option A (Docker); swap in Option B (systemd) below if Docker isn't available on
that server. The rest of this document covers each step's detail and troubleshooting.

**Why `:8080` stays untouched:** 192.168.0.82's public entry point `:8080` is already the EP
(Event Planner) server (AOI/Mission/TLE API - see `docs/ep-server-api-reference.txt` in the
`sat_footprint` DEM-server repo), so `sat_simulation_api` binds to `127.0.0.1:8081` (not directly
reachable from outside) instead of `0.0.0.0:8080`, and reaches the outside world only through the
reverse-proxy path added in step 7-8 (details in "Reverse proxy setup" below).

Everything below (`deploy/deploy.sh`, `deploy/sat-simulation-api.service`, `.env`, etc.) is
relative to the `hk_api/` folder cloned in step 2, not the `sat_footprint` repo root.

## Option A - Docker (recommended)

```bash
HOST_PORT=8081 bash deploy/deploy.sh
```

First run stops after creating `.env` / `config/satellites.toml` from the `*.example` templates
so real DB credentials, `API_KEY`, and `CORS_ALLOWED_ORIGINS` (see below) can be filled in -
fill those in, then run the same command again to build the image and start the container
(`--restart unless-stopped`, so it survives reboots). It binds to `127.0.0.1:8081` only.

## Option B - no Docker (systemd)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && cp config/satellites.example.toml config/satellites.toml
# fill in .env / config/satellites.toml with real values

sudo cp deploy/sat-simulation-api.service /etc/systemd/system/
sudo sed -i "s#/opt/sat_footprint/hk_api#$(pwd)#" /etc/systemd/system/sat-simulation-api.service
sudo sed -i "s#__USER__#$(whoami)#" /etc/systemd/system/sat-simulation-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now sat-simulation-api
```

This also binds to `127.0.0.1:8081` only (see the unit file).

## Reverse proxy setup

Whatever already terminates `192.168.0.82:8080` for the EP server needs one more route added,
forwarding a path (e.g. `/sat-api/`) to `127.0.0.1:8081/`. If that's nginx, the added block
looks like:

```nginx
location /sat-api/ {
    proxy_pass http://127.0.0.1:8081/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

(If it's something other than nginx - IIS, Caddy, whatever the EP server actually runs behind -
the equivalent is a reverse-proxy route for `/sat-api/*` to `http://127.0.0.1:8081/*`, with the
trailing-slash rewrite so `/sat-api/telemetry/query` reaches `/telemetry/query` on this API.)

After that's wired up, the API is reachable at `http://192.168.0.82:8080/sat-api/...` - e.g.
`http://192.168.0.82:8080/sat-api/health`.

Actual `POST` endpoints work through the proxy as-is (nginx strips `/sat-api` before forwarding,
so this API sees plain `/telemetry/query` etc. and doesn't need to know about the prefix). The
one thing that *does* need to know about it is the interactive `/docs` page - by default it
generates links assuming it's mounted at `/`, so under the proxy `/sat-api/docs` would try to
fetch `/openapi.json` instead of `/sat-api/openapi.json`. If working `/docs` under the proxy
matters, pass `--root-path /sat-api` to uvicorn - for Option B, add it to the `ExecStart` line
in `sat-simulation-api.service`; for Option A (Docker), append it as extra args on the `docker
run` line in `deploy.sh` (the image's `CMD` doesn't take it via an env var, only as a command
argument). Purely cosmetic for `/docs` either way - not required for the DEM server's actual
API calls to work.

## After either option

Verify locally on the server first (before the reverse-proxy route exists, this is the only way
to reach it):

```bash
curl -sf http://127.0.0.1:8081/health
```

Then, once the reverse-proxy route above is in place, verify from another machine on the
network through the real public path:

```bash
curl http://192.168.0.82:8080/sat-api/health
```

The DEM server then calls `http://192.168.0.82:8080/sat-api/...` directly - no proxy needed on
its side. This replaces `attitude-viewer`'s current internal proxy (`HK_API_BASE_URL` ->
`/api/hk/*`, calling a locally-run `hk_api` process): once this deployment is up, point
`HK_API_BASE_URL` at `http://192.168.0.82:8080/sat-api` instead, so `hk_api` is called as a
standalone API rather than a process `attitude-viewer` has to run alongside itself. If it fetches
from a browser context instead of server-to-server, its origin (`http://192.168.0.82:8080` - the
`/sat-api` path doesn't matter for CORS) must be added to `CORS_ALLOWED_ORIGINS` in `.env` (see
main `README.md` → CORS section).

If port 8081 on the server turns out to already be taken too, override it at deploy time with
`HOST_PORT=<port> bash deploy/deploy.sh` (Option A) or the `--port` flag in the unit file
(Option B), and point the reverse-proxy `proxy_pass` at the same port.
