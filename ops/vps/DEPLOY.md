# Deploy To VPS (SSH)

This repo is designed to run via Docker Compose.

## One-Time: SSH Key (Recommended)

Prefer SSH keys over passwords for automation.

1. Create a dedicated key (local machine):
```bash
ssh-keygen -t ed25519 -f ~/.ssh/agente_paper_chambeador -N '' -C 'agente-paper@chambeador'
```

2. Copy it to the VPS (you will be prompted for the password once):
```bash
ssh-copy-id -i ~/.ssh/agente_paper_chambeador.pub mushasho@chambeador
```

3. Add a host entry (local machine) in `~/.ssh/config`:
```sshconfig
Host chambeador
  HostName chambeador
  User mushasho
  IdentityFile ~/.ssh/agente_paper_chambeador
  IdentitiesOnly yes
```

Then you can connect with:
```bash
ssh chambeador
```

## Deploy Script

From the repo root:
```bash
bash ops/vps/deploy_vps.sh --host 100.91.0.55 --user mushasho --dir /opt/agente-paper --with-mysql --identity ~/.ssh/agente_paper_chambeador
```

Notes:
- The script does **not** copy your local `.env` by default.
  - If `/opt/agente-paper/.env` does not exist on the VPS, it will create it from `.env.example`.
  - You must edit `/opt/agente-paper/.env` on the VPS with real keys/tokens before the bot will work.
- `--with-mysql` uses compose profile `mysql` (includes seeded MySQL inside compose).
- Production should NOT use `docker-compose.override.yml` (that file is for local dev bind mounts).

## Verify

On the VPS:
```bash
curl -fsS http://localhost:8081/health
curl -fsS http://localhost:7000/health
docker compose ps
```
