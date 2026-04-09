#!/usr/bin/env bash
set -euo pipefail

# NoteWeaver VPS One-Click Deploy Script
# Supports: Ubuntu 22.04+ / Debian 12+
# Usage:
#   curl -sSL https://raw.githubusercontent.com/forjiuzhou/KAgent/main/deploy/setup.sh | bash
#   or: bash deploy/setup.sh

REPO_URL="https://github.com/forjiuzhou/KAgent.git"
NW_USER="noteweaver"
INSTALL_DIR="/home/${NW_USER}/KAgent"
VAULT_DIR="/home/${NW_USER}/vault"
SERVICE_FILE="/etc/systemd/system/noteweaver.service"

info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m    $*"; }
err()   { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        err "This script must be run as root (use sudo)."
        exit 1
    fi
}

install_deps() {
    info "Installing system dependencies..."
    apt-get update -qq
    apt-get install -y -qq git python3 python3-pip python3-venv > /dev/null
    ok "System dependencies installed."
}

create_user() {
    if id "${NW_USER}" &>/dev/null; then
        info "User '${NW_USER}' already exists, skipping."
    else
        info "Creating user '${NW_USER}'..."
        adduser --disabled-password --gecos "" "${NW_USER}"
        ok "User '${NW_USER}' created."
    fi
}

clone_and_install() {
    info "Cloning repository..."
    if [[ -d "${INSTALL_DIR}" ]]; then
        info "Directory exists, pulling latest..."
        sudo -u "${NW_USER}" git -C "${INSTALL_DIR}" pull --ff-only origin main || true
    else
        sudo -u "${NW_USER}" git clone "${REPO_URL}" "${INSTALL_DIR}"
    fi

    info "Installing NoteWeaver (with all extras)..."
    sudo -u "${NW_USER}" bash -c "
        cd ${INSTALL_DIR}
        python3 -m venv ~/.nw-venv
        source ~/.nw-venv/bin/activate
        pip install --upgrade pip -q
        pip install -e '.[all]' -q
    "
    ok "NoteWeaver installed."
}

init_vault() {
    if [[ -d "${VAULT_DIR}/.schema" ]]; then
        info "Vault already initialized, skipping."
    else
        info "Initializing vault..."
        sudo -u "${NW_USER}" bash -c "
            source ~/.nw-venv/bin/activate
            NW_VAULT=${VAULT_DIR} nw init
        "
        ok "Vault initialized at ${VAULT_DIR}."
    fi
}

configure_env() {
    local env_file="/home/${NW_USER}/.noteweaver.env"

    if [[ -f "${env_file}" ]]; then
        info "Environment file already exists at ${env_file}, skipping."
        info "Edit it manually to update API keys."
        return
    fi

    info "Creating environment file..."
    cat > "${env_file}" <<'ENVEOF'
# NoteWeaver Environment Configuration
# Edit this file and then restart the service:
#   sudo systemctl restart noteweaver

# LLM Provider — set at least one API key
OPENAI_API_KEY=your-openai-key-here
# ANTHROPIC_API_KEY=your-anthropic-key-here

# Optional: force a specific provider / model
# NW_PROVIDER=openai
# NW_MODEL=gpt-4o

# Telegram bot (get a token from @BotFather)
NW_TELEGRAM_TOKEN=your-telegram-bot-token-here

# Optional: restrict to specific Telegram user IDs (comma-separated)
# NW_TELEGRAM_ALLOWED_USERS=123456789

# Cron intervals
NW_DIGEST_INTERVAL_HOURS=6
NW_LINT_INTERVAL_HOURS=24
ENVEOF

    chown "${NW_USER}:${NW_USER}" "${env_file}"
    chmod 600 "${env_file}"
    ok "Environment file created at ${env_file}"
    echo ""
    echo "  *** IMPORTANT: Edit ${env_file} to set your API keys ***"
    echo ""
}

install_service() {
    info "Installing systemd service..."
    cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=NoteWeaver Knowledge Harness Gateway
After=network.target

[Service]
Type=simple
User=${NW_USER}
WorkingDirectory=/home/${NW_USER}
EnvironmentFile=/home/${NW_USER}/.noteweaver.env
Environment=NW_VAULT=${VAULT_DIR}
ExecStart=/home/${NW_USER}/.nw-venv/bin/nw gateway
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable noteweaver
    ok "Systemd service installed and enabled."
}

print_summary() {
    echo ""
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║           NoteWeaver — Deployment Complete          ║"
    echo "╠══════════════════════════════════════════════════════╣"
    echo "║                                                      ║"
    echo "║  Next steps:                                         ║"
    echo "║                                                      ║"
    echo "║  1. Edit API keys:                                   ║"
    echo "║     sudo nano /home/${NW_USER}/.noteweaver.env       ║"
    echo "║                                                      ║"
    echo "║  2. Start the service:                               ║"
    echo "║     sudo systemctl start noteweaver                  ║"
    echo "║                                                      ║"
    echo "║  3. Check status:                                    ║"
    echo "║     sudo systemctl status noteweaver                 ║"
    echo "║                                                      ║"
    echo "║  4. View logs:                                       ║"
    echo "║     sudo journalctl -u noteweaver -f                 ║"
    echo "║                                                      ║"
    echo "║  Vault:   ${VAULT_DIR}                               ║"
    echo "║  Config:  /home/${NW_USER}/.noteweaver.env           ║"
    echo "║  Service: /etc/systemd/system/noteweaver.service     ║"
    echo "║                                                      ║"
    echo "╚══════════════════════════════════════════════════════╝"
}

main() {
    echo "=== NoteWeaver VPS Deployment ==="
    echo ""

    check_root
    install_deps
    create_user
    clone_and_install
    init_vault
    configure_env
    install_service
    print_summary
}

main "$@"
