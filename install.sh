#!/usr/bin/env bash
set -e

REPO="https://github.com/fropa/pyxis.git"
DIR="pyxis"
BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

info()  { echo -e "${GREEN}▶ $*${RESET}"; }
warn()  { echo -e "${YELLOW}⚠ $*${RESET}"; }
error() { echo -e "${RED}✗ $*${RESET}"; exit 1; }

echo -e "${BOLD}"
cat <<'EOF'
  ____             _
 |  _ \ _   ___  _(_)___
 | |_) | | | \ \/ / / __|
 |  __/| |_| |>  <| \__ \
 |_|    \__, /_/\_\_|___/
        |___/
 AI-powered infrastructure observability
EOF
echo -e "${RESET}"

# ── 1. Check OS ────────────────────────────────────────────────────────────────
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    error "Cannot detect Linux distribution."
fi

# ── 2. Install Docker if missing ───────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    info "Installing Docker..."
    case "$OS" in
        ubuntu|debian|linuxmint|pop)
            sudo apt-get update -qq
            sudo apt-get install -y -qq ca-certificates curl gnupg lsb-release
            sudo install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/$OS/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/$OS $(lsb_release -cs) stable" | \
                sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
            sudo apt-get update -qq
            sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
            ;;
        centos|rhel|almalinux|rocky)
            sudo yum install -y -q yum-utils
            sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            sudo yum install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
            sudo systemctl enable --now docker
            ;;
        fedora)
            sudo dnf install -y -q dnf-plugins-core
            sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
            sudo dnf install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
            sudo systemctl enable --now docker
            ;;
        arch|manjaro)
            sudo pacman -Sy --noconfirm docker docker-compose
            sudo systemctl enable --now docker
            ;;
        *)
            error "Unsupported distribution: $OS. Install Docker manually: https://docs.docker.com/engine/install/"
            ;;
    esac
    sudo usermod -aG docker "$USER" || true
    info "Docker installed."
else
    info "Docker already installed: $(docker --version)"
fi

# ── 3. Clone repo ──────────────────────────────────────────────────────────────
if [ -d "$DIR" ]; then
    warn "Directory '$DIR' already exists — pulling latest changes."
    cd "$DIR" && git pull --quiet
else
    info "Cloning Pyxis..."
    git clone --quiet "$REPO" "$DIR"
    cd "$DIR"
fi

# ── 4. Configure environment ───────────────────────────────────────────────────
if [ ! -f backend/.env ]; then
    cp backend/.env.example backend/.env
fi

if grep -q "^ANTHROPIC_API_KEY=$" backend/.env || grep -q "^ANTHROPIC_API_KEY=sk-ant-\.\.\." backend/.env; then
    echo ""
    echo -e "${BOLD}Anthropic API key required.${RESET}"
    echo "Get one free at: https://console.anthropic.com"
    read -rp "Paste your Anthropic API key: " ANTHROPIC_KEY
    if [ -n "$ANTHROPIC_KEY" ]; then
        sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$ANTHROPIC_KEY|" backend/.env
    else
        warn "No key provided — AI features (RCA, runbooks, playground) will not work until you add it to backend/.env"
    fi
fi

# ── 5. Start the stack ─────────────────────────────────────────────────────────
info "Starting Pyxis (this may take a few minutes on first run)..."
docker compose up -d --build

# ── 6. Wait for backend ────────────────────────────────────────────────────────
info "Waiting for backend to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    warn "Backend didn't respond in time. Check logs: docker compose logs backend"
else
    # ── 7. Create default tenant ───────────────────────────────────────────────
    RESPONSE=$(curl -sf -X POST http://localhost:8000/api/v1/tenants/ \
        -H "Content-Type: application/json" \
        -d '{"name":"default","contact_email":"admin@localhost"}' 2>/dev/null || echo "")

    if [ -n "$RESPONSE" ]; then
        API_KEY=$(echo "$RESPONSE" | grep -o '"api_key":"[^"]*"' | cut -d'"' -f4)
    else
        # Tenant may already exist — fetch it
        API_KEY=$(curl -sf http://localhost:8000/api/v1/tenants/ 2>/dev/null | grep -o '"api_key":"[^"]*"' | head -1 | cut -d'"' -f4)
    fi

    echo ""
    echo -e "${BOLD}${GREEN}✓ Pyxis is running!${RESET}"
    echo ""
    echo -e "  Dashboard   →  ${BOLD}http://localhost:5173${RESET}"
    echo -e "  API docs    →  ${BOLD}http://localhost:8000/docs${RESET}"
    echo ""
    if [ -n "$API_KEY" ]; then
        echo -e "  Your API key:  ${BOLD}${API_KEY}${RESET}"
        echo ""
        echo "  Paste this key into the Settings page in the dashboard."
        echo ""
        echo "  Or set it immediately in the browser console:"
        echo -e "  ${YELLOW}localStorage.setItem('pyxis-store', JSON.stringify({state:{apiKey:'${API_KEY}'},version:0})); location.reload();${RESET}"
    fi
    echo ""
fi
