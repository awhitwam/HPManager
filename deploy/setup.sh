#!/usr/bin/env bash
set -euo pipefail

# =====================================================
# HPManager - Proxmox LXC Deployment Script
# Run as root inside a fresh Debian 12 LXC container
# with nesting feature enabled.
#
# Usage:
#   bash deploy/setup.sh
#
# Environment variable overrides (all optional):
#   INSTALL_DIR     - Installation path (default: /opt/hpmanager)
#   REPO_URL        - Git clone URL (skipped if already in project dir)
#   HP1_HOST/PORT   - Heat pump 1 address (default: 192.168.8.74:502)
#   HP2_HOST/PORT   - Heat pump 2 address (default: 192.168.8.124:502)
#   INFLUXDB_PASSWORD       - InfluxDB admin password
#   GRAFANA_ADMIN_PASSWORD  - Grafana admin password
#   INFLUXDB_TOKEN          - InfluxDB API token
# =====================================================

# --- Configuration ---
INSTALL_DIR="${INSTALL_DIR:-/opt/hpmanager}"
REPO_URL="${REPO_URL:-}"
HP1_HOST="${HP1_HOST:-192.168.8.74}"
HP1_PORT="${HP1_PORT:-502}"
HP2_HOST="${HP2_HOST:-192.168.8.124}"
HP2_PORT="${HP2_PORT:-502}"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[HPM]${NC} $*"; }
warn() { echo -e "${YELLOW}[HPM]${NC} $*"; }
err()  { echo -e "${RED}[HPM ERROR]${NC} $*" >&2; }
header() { echo -e "\n${BLUE}=== $* ===${NC}"; }

# --- Pre-flight checks ---

check_root() {
    if [[ $EUID -ne 0 ]]; then
        err "This script must be run as root"
        exit 1
    fi
}

check_lxc() {
    if grep -q 'container=lxc' /proc/1/environ 2>/dev/null; then
        log "Running inside LXC container"
    else
        warn "Not running inside an LXC container (this is OK for testing)"
    fi
}

# --- Docker installation ---

install_docker() {
    header "Installing Docker Engine"

    if command -v docker &>/dev/null; then
        log "Docker already installed: $(docker --version)"
    else
        log "Installing prerequisites..."
        apt-get update -qq
        apt-get install -y -qq ca-certificates curl gnupg lsb-release >/dev/null

        log "Adding Docker repository..."
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc

        echo \
          "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
          $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
          tee /etc/apt/sources.list.d/docker.list > /dev/null

        apt-get update -qq
        log "Installing Docker packages..."
        apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null

        log "Docker installed: $(docker --version)"
    fi

    # Ensure Docker starts on boot
    systemctl enable docker >/dev/null 2>&1
    systemctl start docker

    # Verify Docker Compose
    if docker compose version &>/dev/null; then
        log "Docker Compose: $(docker compose version --short)"
    else
        err "Docker Compose plugin not found"
        exit 1
    fi
}

# --- Project setup ---

setup_project() {
    header "Setting up project"

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

    # Check if we're running from within the project
    if [[ -f "$PROJECT_DIR/docker-compose.yml" ]]; then
        if [[ "$PROJECT_DIR" != "$INSTALL_DIR" ]]; then
            log "Copying project to $INSTALL_DIR..."
            mkdir -p "$INSTALL_DIR"
            cp -r "$PROJECT_DIR/." "$INSTALL_DIR/"
        else
            log "Already at $INSTALL_DIR"
        fi
    elif [[ -n "$REPO_URL" ]]; then
        if [[ -d "$INSTALL_DIR/.git" ]]; then
            log "Project already cloned, pulling latest..."
            cd "$INSTALL_DIR"
            git pull
        else
            log "Cloning from $REPO_URL..."
            apt-get install -y -qq git >/dev/null
            git clone "$REPO_URL" "$INSTALL_DIR"
        fi
    else
        err "Cannot find project files."
        err "Either run this script from within the project directory,"
        err "or set REPO_URL to the git repository URL."
        exit 1
    fi

    cd "$INSTALL_DIR"
    log "Working directory: $INSTALL_DIR"
}

# --- Secret generation ---

generate_secrets() {
    header "Configuring credentials"

    # Generate InfluxDB token if not provided
    if [[ -z "${INFLUXDB_TOKEN:-}" ]]; then
        INFLUXDB_TOKEN=$(openssl rand -hex 32)
        log "Generated InfluxDB token"
    else
        log "Using provided InfluxDB token"
    fi

    # InfluxDB password
    if [[ -z "${INFLUXDB_PASSWORD:-}" ]]; then
        if [[ -t 0 ]]; then
            read -sp "Enter InfluxDB admin password (or press Enter to auto-generate): " INFLUXDB_PASSWORD
            echo
        fi
        if [[ -z "${INFLUXDB_PASSWORD:-}" ]]; then
            INFLUXDB_PASSWORD=$(openssl rand -base64 16)
            log "Generated InfluxDB password"
        fi
    else
        log "Using provided InfluxDB password"
    fi

    # Grafana password
    if [[ -z "${GRAFANA_ADMIN_PASSWORD:-}" ]]; then
        if [[ -t 0 ]]; then
            read -sp "Enter Grafana admin password (or press Enter to auto-generate): " GRAFANA_ADMIN_PASSWORD
            echo
        fi
        if [[ -z "${GRAFANA_ADMIN_PASSWORD:-}" ]]; then
            GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 16)
            log "Generated Grafana password"
        fi
    else
        log "Using provided Grafana password"
    fi
}

# --- Environment file ---

create_env_file() {
    header "Creating .env file"

    local ENV_FILE="$INSTALL_DIR/.env"

    if [[ -f "$ENV_FILE" ]]; then
        warn ".env file already exists — backing up to .env.backup"
        cp "$ENV_FILE" "$ENV_FILE.backup"
    fi

    cat > "$ENV_FILE" <<EOF
# HPManager Production Environment
# Generated by deploy/setup.sh on $(date -Iseconds)

# InfluxDB
INFLUXDB_URL=http://influxdb:8086
INFLUXDB_ORG=heatpump-monitoring
INFLUXDB_BUCKET=heatpump-data
INFLUXDB_TOKEN=${INFLUXDB_TOKEN}
INFLUXDB_USERNAME=admin
INFLUXDB_PASSWORD=${INFLUXDB_PASSWORD}

# Grafana
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}

# Webapp
CONFIG_DIR=/app/config
WEBAPP_PORT=8080
EOF

    chmod 600 "$ENV_FILE"
    log "Created $ENV_FILE (permissions: 600)"
}

# --- Heat pump config ---

update_heatpumps_config() {
    header "Configuring heat pump addresses"

    local PROD_CONFIG="$INSTALL_DIR/config/heatpumps.production.yml"
    local CONFIG="$INSTALL_DIR/config/heatpumps.yml"

    if [[ -f "$PROD_CONFIG" ]]; then
        cp "$PROD_CONFIG" "$CONFIG"
        log "Copied production config (direct LAN addresses)"
    else
        warn "heatpumps.production.yml not found, using existing config"
    fi

    # Ensure webapp container (UID 1000) can write config files
    chown -R 1000:1000 "$INSTALL_DIR/config"
    log "Set config directory ownership to UID 1000 (webapp)"

    log "HP1: ${HP1_HOST}:${HP1_PORT}"
    log "HP2: ${HP2_HOST}:${HP2_PORT}"
}

# --- Build and start ---

build_and_start() {
    header "Building and starting services"

    cd "$INSTALL_DIR"

    log "Building Docker images..."
    docker compose build --quiet

    log "Starting services..."
    docker compose up -d

    log "Services started"
}

# --- Health check ---

wait_for_healthy() {
    header "Waiting for services to become healthy"

    local MAX_WAIT=120
    local ELAPSED=0

    while [[ $ELAPSED -lt $MAX_WAIT ]]; do
        local UNHEALTHY
        UNHEALTHY=$(docker compose ps --format json 2>/dev/null | \
            python3 -c "
import sys, json
unhealthy = []
for line in sys.stdin:
    svc = json.loads(line)
    health = svc.get('Health', '')
    if health and health != 'healthy':
        unhealthy.append(svc.get('Service', svc.get('Name', '?')))
print(','.join(unhealthy))
" 2>/dev/null || echo "unknown")

        if [[ -z "$UNHEALTHY" ]]; then
            log "All services healthy!"
            return 0
        fi

        echo -ne "\r  Waiting... ${ELAPSED}s (pending: ${UNHEALTHY})    "
        sleep 5
        ELAPSED=$((ELAPSED + 5))
    done

    echo
    warn "Some services may not be healthy yet (timeout after ${MAX_WAIT}s)"
    docker compose ps
}

# --- Summary ---

print_summary() {
    header "Deployment complete!"

    local IP
    IP=$(hostname -I | awk '{print $1}')

    echo ""
    echo -e "  ${GREEN}Web Dashboard:${NC}  http://${IP}:8080"
    echo -e "  ${GREEN}Grafana:${NC}        http://${IP}:3000"
    echo -e "  ${GREEN}InfluxDB:${NC}       http://${IP}:8086"
    echo ""
    echo -e "  ${YELLOW}Grafana login:${NC}  admin / ${GRAFANA_ADMIN_PASSWORD}"
    echo -e "  ${YELLOW}InfluxDB login:${NC} admin / ${INFLUXDB_PASSWORD}"
    echo ""
    echo -e "  Credentials saved to: ${INSTALL_DIR}/.env"
    echo ""
    echo -e "  Useful commands:"
    echo -e "    cd ${INSTALL_DIR}"
    echo -e "    docker compose logs -f          # View all logs"
    echo -e "    docker compose logs -f collector # Collector logs only"
    echo -e "    docker compose ps               # Service status"
    echo -e "    docker compose restart           # Restart all services"
    echo ""
}

# --- Main ---

main() {
    echo -e "${BLUE}"
    echo "  _   _ ____  __  __"
    echo " | | | |  _ \\|  \\/  | __ _ _ __   __ _  __ _  ___ _ __"
    echo " | |_| | |_) | |\\/| |/ _\` | '_ \\ / _\` |/ _\` |/ _ \\ '__|"
    echo " |  _  |  __/| |  | | (_| | | | | (_| | (_| |  __/ |"
    echo " |_| |_|_|   |_|  |_|\\__,_|_| |_|\\__,_|\\__, |\\___|_|"
    echo "                                        |___/"
    echo -e "${NC}"
    echo "  Proxmox LXC Deployment Script"
    echo ""

    check_root
    check_lxc
    install_docker
    setup_project
    generate_secrets
    create_env_file
    update_heatpumps_config
    build_and_start
    wait_for_healthy
    print_summary
}

main "$@"
