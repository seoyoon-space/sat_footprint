# Deploying to a test server (e.g. 192.168.0.82:8080)

Run these steps **on the target server itself** over SSH - this cannot be done remotely from a
dev machine, since it needs that server's own filesystem/Docker/credentials.

192.168.0.82 is the team's shared dev server. **The public entry point stays `:8080`** - that's
already the EP (Event Planner) server (AOI/Mission/TLE API - see
`docs/ep-server-api-reference.txt` in the `sat_footprint` DEM-server repo), so
`sat_simulation_api` does not bind to `0.0.0.0:8080` itself. Instead it runs on
`127.0.0.1:8081` (not reachable from outside directly) and is exposed to the outside world
through a path added to whatever already fronts port 8080 - see "Reverse proxy setup" below.

```bash
ssh <user>@192.168.0.82
git clone https://github.com/seoyoon-space/sat_footprint.git
cd sat_footprint
git checkout doeun-space
```

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
sudo sed -i "s#/opt/sat_footprint#$(pwd)#" /etc/systemd/system/sat-simulation-api.service
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
its side. If it fetches from a browser context instead of server-to-server, its origin
(`http://192.168.0.82:8080` - the origin is scheme+host+port, the `/sat-api` path doesn't
matter for CORS) must be added to `CORS_ALLOWED_ORIGINS` in `.env` (see main `README.md` → CORS
section).

If port 8081 on the server turns out to already be taken too, override it at deploy time with
`HOST_PORT=<port> bash deploy/deploy.sh` (Option A) or the `--port` flag in the unit file
(Option B), and point the reverse-proxy `proxy_pass` at the same port.
