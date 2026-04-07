#!/usr/bin/env bash
set -e

REPO="https://github.com/fropa/pyxis.git"
DIR="/opt/pyxis"
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

# ── 3. Install nginx if missing ────────────────────────────────────────────────
if ! command -v nginx &>/dev/null; then
    info "Installing nginx..."
    case "$OS" in
        ubuntu|debian|linuxmint|pop)
            sudo apt-get install -y -qq nginx ;;
        centos|rhel|almalinux|rocky)
            sudo yum install -y -q nginx
            sudo systemctl enable --now nginx ;;
        fedora)
            sudo dnf install -y -q nginx
            sudo systemctl enable --now nginx ;;
        arch|manjaro)
            sudo pacman -Sy --noconfirm nginx
            sudo systemctl enable --now nginx ;;
    esac
    info "nginx installed."
else
    info "nginx already installed: $(nginx -v 2>&1)"
fi

# ── 4. Clone or update repo ────────────────────────────────────────────────────
if [ -d "$DIR" ]; then
    info "Updating Pyxis..."
    cd "$DIR" && sudo git pull --quiet
else
    info "Cloning Pyxis into $DIR..."
    sudo git clone --quiet "$REPO" "$DIR"
    cd "$DIR"
fi

# ── 5. Configure environment ───────────────────────────────────────────────────
if [ ! -f backend/.env ]; then
    cp backend/.env.example backend/.env
fi

# Check if ANTHROPIC_API_KEY is already a real key (starts with sk-ant-)
CURRENT_KEY=$(grep "^ANTHROPIC_API_KEY=" backend/.env | cut -d'=' -f2-)
OFFLINE_MODE=false

if [[ "$CURRENT_KEY" == sk-ant-* ]]; then
    MASKED="${CURRENT_KEY:0:18}…${CURRENT_KEY: -4}"
    info "Anthropic API key already configured: ${MASKED}"
    printf "  Press Enter to keep it, or paste a new key: " >&2
    read -r NEW_KEY </dev/tty
    if [ -n "$NEW_KEY" ]; then
        sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$NEW_KEY|" backend/.env
        info "API key updated."
    fi
else
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}║         Anthropic API Key Setup              ║${RESET}"
    echo -e "${BOLD}╚══════════════════════════════════════════════╝${RESET}"
    echo ""
    echo "  Pyxis uses Claude AI for root cause analysis, runbooks,"
    echo "  post-mortems, and the on-call assistant."
    echo ""
    echo "  Get a free key at: https://console.anthropic.com"
    echo ""
    echo -e "  ${YELLOW}[O]ffline${RESET} — install without AI features (add the key later)"
    echo -e "  ${GREEN}[key]${RESET}    — paste your Anthropic API key now"
    echo ""
    printf "  Your choice: " >&2
    read -r INPUT </dev/tty

    if [[ "${INPUT,,}" == "o" || "${INPUT,,}" == "offline" || -z "$INPUT" ]]; then
        OFFLINE_MODE=true
        warn "Offline mode — AI features disabled. Add ANTHROPIC_API_KEY to backend/.env later to enable them."
    elif [[ "$INPUT" == sk-ant-* ]]; then
        sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$INPUT|" backend/.env
        info "API key saved."
    else
        warn "Key doesn't look right (expected sk-ant-...). Saved anyway — check backend/.env if AI doesn't work."
        sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$INPUT|" backend/.env
    fi
fi

# ── 6. Start the stack ─────────────────────────────────────────────────────────
info "Starting Pyxis (this may take a few minutes on first run)..."
docker compose up -d --build

# ── 7. Wait for backend ────────────────────────────────────────────────────────
info "Waiting for backend to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    warn "Backend didn't respond in time. Check: docker compose logs backend"
    exit 1
fi

# ── 8. Create default tenant ───────────────────────────────────────────────────
RESPONSE=$(curl -sf -X POST http://localhost:8000/api/v1/tenants/ \
    -H "Content-Type: application/json" \
    -d '{"name":"default","contact_email":"admin@localhost"}' 2>/dev/null || echo "")

if [ -n "$RESPONSE" ]; then
    API_KEY=$(echo "$RESPONSE" | grep -o '"api_key":"[^"]*"' | cut -d'"' -f4)
else
    API_KEY=$(curl -sf http://localhost:8000/api/v1/tenants/ 2>/dev/null | grep -o '"api_key":"[^"]*"' | head -1 | cut -d'"' -f4)
fi

# ── 9. Configure nginx ─────────────────────────────────────────────────────────
info "Configuring nginx..."

SERVER_IP=$(hostname -I | awk '{print $1}')

NGINX_CONF=/etc/nginx/sites-available/pyxis

sudo tee "$NGINX_CONF" > /dev/null <<NGINX
server {
    listen 80 default_server;
    server_name _;

    # Backend routes → FastAPI :8000
    location /api/ {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host            \$host;
        proxy_set_header   X-Real-IP       \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }

    location /ws/ {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    \$http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host       \$host;
        proxy_read_timeout 3600s;
    }

    location /docs {
        proxy_pass       http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
    }

    location /openapi.json {
        proxy_pass       http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
    }

    location /redoc {
        proxy_pass       http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
    }

    location /install {
        proxy_pass       http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
    }

    location /health {
        proxy_pass       http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
    }

    # Everything else → Vite frontend :5173
    location / {
        proxy_pass         http://127.0.0.1:5173;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    \$http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host       \$host;
    }
}
NGINX

# Enable site — handle both sites-enabled (Debian/Ubuntu) and conf.d (RHEL/Fedora/Arch)
if [ -d /etc/nginx/sites-enabled ]; then
    sudo ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/pyxis
    # Disable the default site so it doesn't conflict on port 80
    sudo rm -f /etc/nginx/sites-enabled/default
elif [ -d /etc/nginx/conf.d ]; then
    sudo ln -sf "$NGINX_CONF" /etc/nginx/conf.d/pyxis.conf
    # Remove the default welcome page config
    sudo rm -f /etc/nginx/conf.d/default.conf
fi

if sudo nginx -t 2>/dev/null; then
    sudo systemctl reload nginx
    info "nginx configured and reloaded."
else
    warn "nginx config test failed — check: sudo nginx -t"
fi

# ── 10. Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}✓ Pyxis is running!${RESET}"
echo ""
echo -e "  Dashboard  →  ${BOLD}http://${SERVER_IP}${RESET}"
echo -e "  API docs   →  ${BOLD}http://${SERVER_IP}/docs${RESET}"
echo ""
if [ -n "$API_KEY" ]; then
    echo -e "  API key:  ${BOLD}${API_KEY}${RESET}"
    echo ""
    echo "  Paste into Settings, or run in browser console:"
    echo -e "  ${YELLOW}localStorage.setItem('pyxis-store', JSON.stringify({state:{apiKey:'${API_KEY}'},version:0})); location.reload();${RESET}"
fi
echo ""
