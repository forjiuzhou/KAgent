# Deploying NoteWeaver to a VPS

## Quick Setup

```bash
# On a fresh Ubuntu VPS ($5/month is plenty)

# 1. Create user
sudo adduser noteweaver
sudo su - noteweaver

# 2. Install
git clone https://github.com/forjiuzhou/KAgent.git
cd KAgent
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
# Edit the service file to set your actual API keys
sudo systemctl daemon-reload
sudo systemctl enable noteweaver
sudo systemctl start noteweaver

# Check status
sudo systemctl status noteweaver
sudo journalctl -u noteweaver -f  # live logs
```

## Syncthing (vault sync to Mac)

```bash
# On VPS
sudo apt install syncthing
syncthing  # first run, then Ctrl+C
# Edit ~/.config/syncthing/config.xml: set GUI listen to 0.0.0.0:8384

# On Mac
brew install syncthing
# Open http://localhost:8384, add the VPS as remote device
# Share the vault directory

# Both will now sync in real-time
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes* | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | Yes* | — | Anthropic API key (alternative to OpenAI) |
| `NW_TELEGRAM_TOKEN` | For Telegram | — | Telegram bot token from @BotFather |
| `NW_TELEGRAM_ALLOWED_USERS` | No | all | Comma-separated Telegram user IDs |
| `NW_VAULT` | No | ./vault | Vault directory path |
| `NW_MODEL` | No | auto | Model name |
| `NW_DIGEST_INTERVAL_HOURS` | No | 6 | Hours between automatic digest |
| `NW_LINT_INTERVAL_HOURS` | No | 24 | Hours between automatic lint |

*One of OPENAI_API_KEY or ANTHROPIC_API_KEY is required.
