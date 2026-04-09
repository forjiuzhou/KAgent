# Deploying NoteWeaver

Two deployment methods are provided: **Docker** (recommended) and **traditional VPS** with systemd.

---

## Method 1: Docker Compose (Recommended)

### Prerequisites

- Docker Engine 24+ and Docker Compose v2+
- An LLM API key (OpenAI or Anthropic)
- (Optional) A Telegram bot token from [@BotFather](https://t.me/BotFather)

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/forjiuzhou/NoteWeaver.git
cd NoteWeaver

# 2. Create environment file
cp .env.example .env
# Edit .env — set your API keys and Telegram token
nano .env

# 3. Initialize vault (one-time)
docker compose run --rm noteweaver init

# 4. Launch the gateway
docker compose up -d

# 5. Check status
docker compose logs -f
```

### Management

```bash
# Stop
docker compose down

# Restart
docker compose restart

# Update to latest version
git pull
docker compose build
docker compose up -d

# Access the vault data
docker volume inspect noteweaver_vault_data
# The volume is mounted at /data/vault inside the container

# Run a one-off command (e.g. nw status)
docker compose run --rm noteweaver status
```

### Custom Vault Path

To use a host directory instead of a Docker volume:

```yaml
# docker-compose.yml — replace the volumes section:
services:
  noteweaver:
    volumes:
      - /path/to/your/vault:/data/vault
```

---

## Method 2: Automated VPS Setup (systemd)

### One-Line Install

```bash
# On a fresh Ubuntu 22.04+ / Debian 12+ VPS (run as root):
curl -sSL https://raw.githubusercontent.com/forjiuzhou/NoteWeaver/main/deploy/setup.sh | sudo bash
```

This script will:
1. Install system dependencies (git, python3, pip, venv)
2. Create a `noteweaver` user
3. Clone the repository and install in a virtualenv
4. Initialize the vault
5. Create an environment file at `/home/noteweaver/.noteweaver.env`
6. Install and enable a systemd service

### Post-Install

```bash
# 1. Set your API keys
sudo nano /home/noteweaver/.noteweaver.env

# 2. Start the service
sudo systemctl start noteweaver

# 3. Check status
sudo systemctl status noteweaver
sudo journalctl -u noteweaver -f
```

### Manual VPS Setup

If you prefer to do it manually:

```bash
# 1. Create user
sudo adduser noteweaver
sudo su - noteweaver

# 2. Install
git clone https://github.com/forjiuzhou/NoteWeaver.git
cd NoteWeaver
python3 -m venv ~/.nw-venv
source ~/.nw-venv/bin/activate
pip install -e ".[all]"

# 3. Initialize vault
nw init

# 4. Configure
export OPENAI_API_KEY=sk-...
export NW_TELEGRAM_TOKEN=your-bot-token

# 5. Test
nw gateway  # Ctrl+C to stop after confirming it works

# 6. Install as system service
sudo cp deploy/noteweaver.service /etc/systemd/system/
sudo nano /etc/systemd/system/noteweaver.service  # set API keys
sudo systemctl daemon-reload
sudo systemctl enable --now noteweaver
```

---

## Syncthing (Vault Sync to Local Machine)

```bash
# On VPS
sudo apt install syncthing
syncthing  # first run, then Ctrl+C
# Edit ~/.config/syncthing/config.xml: set GUI listen to 0.0.0.0:8384

# On Mac
brew install syncthing
# Open http://localhost:8384, add the VPS as remote device
# Share the vault directory — both will now sync in real-time
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes* | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | Yes* | — | Anthropic API key (alternative to OpenAI) |
| `OPENAI_BASE_URL` | No | — | Custom OpenAI-compatible endpoint |
| `ANTHROPIC_BASE_URL` | No | — | Custom Anthropic endpoint |
| `NW_PROVIDER` | No | auto | Force `openai` or `anthropic` |
| `NW_MODEL` | No | auto | LLM model name |
| `NW_TELEGRAM_TOKEN` | For Telegram | — | Telegram bot token from @BotFather |
| `NW_TELEGRAM_ALLOWED_USERS` | No | all | Comma-separated Telegram user IDs |
| `NW_VAULT` | No | ./vault | Vault directory path |
| `NW_DIGEST_INTERVAL_HOURS` | No | 6 | Hours between automatic digest |
| `NW_LINT_INTERVAL_HOURS` | No | 24 | Hours between automatic lint |
| `NW_NOTIFY_HOUR` | No | 9 | Hour (0–23) at which gateway sends batched notifications |

\* One of `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) is required.

---

## File Reference

| File | Purpose |
|------|---------|
| `Dockerfile` | Container image build |
| `docker-compose.yml` | Compose orchestration with env vars |
| `.env.example` | Template for environment variables |
| `deploy/setup.sh` | Automated VPS setup script |
| `deploy/noteweaver.service` | systemd unit file |
