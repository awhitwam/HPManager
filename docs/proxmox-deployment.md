# Proxmox LXC Deployment Guide

Deploy HPManager in a lightweight Proxmox LXC container with Docker. This gives the containers direct LAN access to your heat pumps via Modbus TCP.

## Prerequisites

- Proxmox VE 7.x or 8.x
- Debian 12 (Bookworm) CT template downloaded on your Proxmox host
- Network bridge (vmbr0) with access to your heat pumps' subnet (192.168.8.x)
- Project files available (git clone or file copy)

## Step 1: Create the LXC Container

### Option A: Proxmox Web UI

1. Go to **Datacenter > [your node] > Create CT**
2. **General:**
   - CT ID: e.g. `200`
   - Hostname: `hpmanager`
   - Password: set a root password
   - **Unprivileged container: unchecked** (privileged, required for Docker)
3. **Template:** `debian-12-standard`
4. **Disks:**
   - Root disk: **32 GB** (InfluxDB data grows over time)
5. **CPU:** 2 cores
6. **Memory:** 2048 MB (2 GB), Swap: 512 MB
7. **Network:**
   - Bridge: `vmbr0`
   - IPv4: Static (e.g. `192.168.8.50/24`) or DHCP
   - Gateway: `192.168.8.1`
8. **Confirm and create** (don't start yet)

### Enable Docker features

Select the container > **Options > Features:**
- Enable **Nesting** (required for Docker)

Or via CLI:
```bash
pct set 200 --features nesting=1
```

### Enable auto-start

Select the container > **Options > Start at boot: Yes**

### Option B: CLI one-liner

```bash
pct create 200 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname hpmanager \
  --cores 2 \
  --memory 2048 \
  --swap 512 \
  --rootfs local-lvm:32 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.8.50/24,gw=192.168.8.1 \
  --features nesting=1 \
  --onboot 1
```

## Step 2: Start and Enter the Container

```bash
pct start 200
pct enter 200
```

Or via SSH:
```bash
ssh root@192.168.8.50
```

## Step 3: Run the Deployment Script

```bash
# Install git
apt-get update && apt-get install -y git

# Clone the project
git clone https://github.com/YOUR_USER/HPManager.git /opt/hpmanager

# Run the setup script
cd /opt/hpmanager
bash deploy/setup.sh
```

The script will:
1. Install Docker Engine and Docker Compose
2. Generate secure credentials (or prompt you for passwords)
3. Create the `.env` file
4. Configure heat pump addresses for direct LAN access
5. Build and start all 4 services
6. Wait for health checks to pass
7. Print access URLs and credentials

### Custom heat pump addresses

If your heat pumps are at different IPs, override before running:
```bash
HP1_HOST=10.0.0.100 HP2_HOST=10.0.0.101 bash deploy/setup.sh
```

### Non-interactive mode

For automated deployments, pre-set all credentials:
```bash
INFLUXDB_PASSWORD=MySecretPass123 \
GRAFANA_ADMIN_PASSWORD=GrafanaPass456 \
bash deploy/setup.sh
```

## Step 4: Verify Deployment

### Check service status
```bash
cd /opt/hpmanager
docker compose ps
```

All 4 services should show `Up` and `healthy`:
```
NAME             STATUS                   PORTS
hpm-collector    Up (healthy)
hpm-grafana      Up (healthy)             0.0.0.0:3000->3000/tcp
hpm-influxdb     Up (healthy)             0.0.0.0:8086->8086/tcp
hpm-webapp       Up (healthy)             0.0.0.0:8080->8000/tcp
```

### Check collector logs
```bash
docker compose logs -f collector
```

You should see successful Modbus polls:
```
INFO: Polling hp1 (192.168.8.74:502)...
INFO: hp1: 25 metrics collected
INFO: Polling hp2 (192.168.8.124:502)...
INFO: hp2: 25 metrics collected
```

### Access the services

| Service | URL | Login |
|---------|-----|-------|
| Web Dashboard | http://192.168.8.50:8080 | (no auth) |
| Grafana | http://192.168.8.50:3000 | admin / (your password) |
| InfluxDB | http://192.168.8.50:8086 | admin / (your password) |

Credentials are stored in `/opt/hpmanager/.env`.

## Step 5: Test Modbus connectivity

If the collector shows connection errors, verify network access from inside the LXC:

```bash
# Install netcat for testing
apt-get install -y netcat-openbsd

# Test Modbus TCP port on each heat pump
nc -zv 192.168.8.74 502
nc -zv 192.168.8.124 502
```

Both should report `Connection ... succeeded!`. If not, check:
- LXC network bridge configuration
- Firewall rules on Proxmox host
- Heat pump ISG web interface is enabled and accessible

## Backup

### Proxmox vzdump (recommended)

Back up the entire LXC including Docker volumes and config:

```bash
# Manual backup
vzdump 200 --compress zstd --storage local

# Or schedule via Datacenter > Backup in the Proxmox UI
```

Recommended schedule: **daily**, retain 7 days.

This captures everything: InfluxDB data, Grafana dashboards, configuration files, and credentials.

### Config files only

For a lightweight backup of just the configuration:
```bash
tar czf /root/hpmanager-config-backup.tar.gz \
  /opt/hpmanager/config/ \
  /opt/hpmanager/.env \
  /opt/hpmanager/grafana/
```

## Updating

```bash
cd /opt/hpmanager
git pull
docker compose build
docker compose up -d
```

To rebuild from scratch (preserves data):
```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

## Resource usage

Typical resource consumption on a quiet system:
- **CPU:** < 5% (spikes during Modbus polls every 10s)
- **RAM:** ~800 MB total across all containers
- **Disk:** ~500 MB for images + growing InfluxDB data (~50 MB/day)

## Troubleshooting

### Docker won't start in LXC

Ensure nesting is enabled:
```bash
# On the Proxmox host (not inside the LXC)
pct set 200 --features nesting=1
pct reboot 200
```

### InfluxDB not initialising

InfluxDB only runs setup on first start. If you need to reset:
```bash
cd /opt/hpmanager
docker compose down
docker volume rm hpmanager_influxdb-data hpmanager_influxdb-config
docker compose up -d
```

### Grafana shows "Datasource error"

Check that the InfluxDB token matches between `.env` and what InfluxDB was initialised with. If they've diverged, reset InfluxDB (see above).

### Services don't start after reboot

Docker should auto-start, and all containers have `restart: unless-stopped`. Verify:
```bash
systemctl is-enabled docker
docker compose ps
```

If containers were stopped with `docker compose down`, restart them:
```bash
cd /opt/hpmanager
docker compose up -d
```

Note: Use `docker compose stop` (not `down`) if you want services to auto-restart on next boot.
