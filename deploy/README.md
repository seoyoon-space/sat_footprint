# Deploying to a test server (e.g. 192.168.0.82:8081)

Run these steps **on the target server itself** over SSH - this cannot be done remotely from a
dev machine, since it needs that server's own filesystem/Docker/credentials.

192.168.0.82 is the team's shared dev server, and port 8080 on it is already used by the EP
(Event Planner) server (AOI/Mission/TLE API - see `docs/ep-server-api-reference.txt` in the
`sat_footprint` DEM-server repo). `sat_simulation_api` deploys to **8081** on the same host
instead of colliding with that.

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
so real DB credentials, `API_KEY`, and (if a browser fetches this API directly)
`CORS_ALLOWED_ORIGINS` can be filled in - fill those in, then run the same command again to
build the image and start the container (`--restart unless-stopped`, so it survives reboots).

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

## After either option

Open the port and verify from another machine on the network:

```bash
sudo ufw allow 8081/tcp   # or firewall-cmd --add-port=8081/tcp --permanent && --reload
curl http://192.168.0.82:8081/health
```

The DEM server then calls `http://192.168.0.82:8081/...` directly - no proxy needed unless it's
fetching from a browser context, in which case its origin must be added to
`CORS_ALLOWED_ORIGINS` in `.env` (see main `README.md` → CORS section).

If a different port than 8081 turns out to already be taken too, override it at deploy time
with `HOST_PORT=<port> bash deploy/deploy.sh` rather than editing the script.
