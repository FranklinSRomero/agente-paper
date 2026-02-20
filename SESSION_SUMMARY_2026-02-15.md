# Session Summary (2026-02-15) - VPS Deploy + Small VM Hardening

## Objective
Deploy `agente-paper` to a GCP VPS reachable over Tailscale, make SSH access non-interactive for future sessions, and get the Docker Compose stack running reliably on a small VM.

## VPS Details (as used in this session)
- User: `mushasho`
- Tailscale IP (at time of deploy): `100.91.0.55`
- Tailscale hostname: `chambeador.pompano-vega.ts.net`
- Repo dir on VPS: `/home/mushasho/agente-paper`

## What Was Done

### 1) SSH automation (key-based)
- Created an SSH key locally:
  - `~/.ssh/agente_paper_chambeador`
  - `~/.ssh/agente_paper_chambeador.pub`
- Added an SSH alias on the local machine:
  - `~/.ssh/config` includes:
    - `Host chambeador`
    - `HostName chambeador.pompano-vega.ts.net`
    - `User mushasho`
    - `IdentityFile ~/.ssh/agente_paper_chambeador`
- Installed the public key on the VPS in `~mushasho/.ssh/authorized_keys`.
  - Important: `authorized_keys` must contain the key on a **single line**. Split keys are rejected by `sshd`.

### 2) Deploy tooling added to repo
Added:
- `ops/vps/DEPLOY.md`: deployment notes + SSH key recommendations.
- `ops/vps/deploy_vps.sh`: tar-over-ssh deploy script.
  - Updated to avoid using `docker-compose.override.yml` in production.
  - Updated to support `--identity <path>` so it can use a specific SSH key non-interactively.

### 3) Environment and secrets handling
- Copied the local `.env` to the VPS at:
  - `/home/mushasho/agente-paper/.env`
- Note: avoid printing secrets in logs/chats. In this session, accidental secret pastes happened; recommended action is immediate secret rotation.

### 4) Docker installation on VPS
- The VPS initially did not have Docker.
- Installed Docker from Ubuntu packages (resulting versions observed on VPS):
  - Docker Engine `28.2.2` (Ubuntu build)
  - Docker Compose `2.37.1+ds1`

### 5) Stability issues on a small VM (root cause + mitigation)
Symptoms:
- SSH became unresponsive.
- Tailscale admin console not reachable.
- `docker`/`containerd` errors in serial logs:
  - health checks timing out
  - `copy stream failed`
  - many `context deadline exceeded` / `ttrpc ... inactive stream`

Constraints:
- VM RAM ~ `958Mi`, no swap.

Mitigation implemented:
- Added `ops/vps/docker-compose.vps.yml` with "small VM" defaults:
  - `worker` is disabled unless you explicitly enable the `vision` profile.
  - MySQL is tuned to use less memory (smaller InnoDB buffer pool, disabled perf schema, etc).
- Launched stack using:
  - `docker compose -f docker-compose.yml -f ops/vps/docker-compose.vps.yml --profile mysql up -d`

## Current Runtime State (VPS)
Containers (expected):
- `bot_gateway` -> host port `8081`
- `mcp_server` -> host port `7000`
- `mysql` -> host port `3306`
- `redis`
- `worker` disabled by default on small VM (`profiles: ["vision"]`)

Verified (from VPS):
- `curl http://localhost:8081/health` -> `{"status":"ok"}`
- `curl http://localhost:7000/health` -> `{"status":"ok"}`
- `docker compose ps` showed `healthy` for `bot_gateway`, `mcp_server`, `mysql`, `redis`.

## Repro Commands (Future Sessions)

### Connect
Prefer the Tailscale IP on unstable DNS:
```bash
ssh -i ~/.ssh/agente_paper_chambeador mushasho@100.91.0.55
```

### Deploy / Update code + restart services (small VM mode)
From local repo:
```bash
bash ops/vps/deploy_vps.sh \
  --host 100.91.0.55 \
  --user mushasho \
  --dir /home/mushasho/agente-paper \
  --with-mysql \
  --identity ~/.ssh/agente_paper_chambeador
```

On the VPS (restart without re-copy):
```bash
cd /home/mushasho/agente-paper
docker compose -f docker-compose.yml -f ops/vps/docker-compose.vps.yml --profile mysql up -d
docker compose -f docker-compose.yml -f ops/vps/docker-compose.vps.yml --profile mysql ps
curl -fsS http://localhost:8081/health
curl -fsS http://localhost:7000/health
```

### Enable vision worker (only if VM can handle it)
```bash
cd /home/mushasho/agente-paper
docker compose -f docker-compose.yml -f ops/vps/docker-compose.vps.yml --profile mysql --profile vision up -d
```

## Open Risks / Notes
- On ~1GB RAM with no swap, running `mysql + bot + mcp + redis` is borderline; enabling `worker` may destabilize the VM.
- If SSH becomes unresponsive again:
  - Stop Docker/containers first if possible, then revisit memory usage and consider adding swap.

