# HPManager - Heat Pump Monitoring System

A comprehensive monitoring system for heat pumps that communicates via Modbus, stores time-series data in InfluxDB, and visualizes metrics through Grafana.

## Features

- **Modbus Communication**: Support for both TCP and RTU connections
- **Real-time Data Collection**: Concurrent polling of multiple heat pumps
- **Time-Series Storage**: InfluxDB for efficient metrics storage
- **Visualization**: Grafana dashboards for monitoring and analysis
- **Resilient Design**: Automatic retry logic and error handling
- **Configurable**: YAML-based configuration for easy customization

## Architecture

The system consists of three main services:

1. **Collector**: Python service that polls heat pumps via Modbus and writes data to InfluxDB
2. **InfluxDB**: Time-series database for storing heat pump metrics
3. **Grafana**: Visualization platform for creating dashboards and monitoring

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local development)
- Access to heat pump Modbus interface

### Configuration

1. **Configure Heat Pumps** (`config/heatpumps.yml`):
   - Add your heat pump IP addresses and Modbus settings
   - Set unique IDs and friendly names

2. **Configure Registers** (`config/registers.yml`):
   - Populate with Modbus register addresses from your heat pump documentation
   - Define data types, scales, and units

3. **Configure Collector** (`config/collector.yml`):
   - Adjust polling intervals
   - Set InfluxDB connection parameters
   - Configure logging preferences

### Running with Docker Compose

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f collector

# Stop services
docker-compose down
```

### Access Services

- **InfluxDB UI**: http://localhost:8086
  - Username: `admin`
  - Password: `adminpassword`

- **Grafana**: http://localhost:3000
  - Username: `admin`
  - Password: `admin`

## Development

### Local Setup

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run collector locally
python -m src.collector.main
```

### Project Structure

```
HPManager/
├── src/
│   ├── collector/          # Data collection service
│   │   ├── main.py        # Entry point and orchestration
│   │   ├── modbus_client.py   # Modbus communication
│   │   ├── heatpump.py    # Heat pump device model
│   │   └── influx_writer.py   # InfluxDB client
│   └── webapp/            # Web interface (optional)
├── config/
│   ├── heatpumps.yml     # Heat pump configurations
│   ├── registers.yml     # Modbus register mappings
│   └── collector.yml     # Collector settings
├── docker/
│   └── Dockerfile.collector   # Collector container
├── grafana/
│   ├── dashboards/       # Dashboard definitions
│   └── provisioning/     # Grafana configuration
└── docker-compose.yml    # Service orchestration
```

## Configuration Guide

### Heat Pump Configuration

Edit `config/heatpumps.yml`:

```yaml
heatpumps:
  - id: hp1
    name: Main Heat Pump
    location: Basement
    model: your_model_name
    enabled: true
    modbus:
      type: tcp
      host: 192.168.1.100
      port: 502
      unit_id: 1
      timeout: 5
      retries: 3
```

### Register Mapping

Edit `config/registers.yml` with your heat pump's Modbus register addresses:

```yaml
models:
  your_model_name:
    description: "Your heat pump model"
    registers:
      - name: supply_temperature
        address: 100  # Your register address
        register_type: holding
        data_type: int16
        unit: celsius
        scale: 0.1
```

Refer to your heat pump's Modbus documentation for specific register addresses.

## Monitoring

### InfluxDB Data Explorer

1. Navigate to http://localhost:8086
2. Use the Data Explorer to query `heatpump_metrics`
3. View real-time data from your heat pumps

### Grafana Dashboards

1. Navigate to http://localhost:3000
2. Create dashboards using the pre-configured InfluxDB data source
3. Query the `heatpump_metrics` measurement with tags:
   - `heat_pump_id`: Unique heat pump identifier
   - `name`: Friendly name
   - `location`: Physical location
   - `model`: Heat pump model

## Troubleshooting

### Collector Not Starting

- Check logs: `docker-compose logs collector`
- Verify InfluxDB is healthy: `docker-compose ps`
- Ensure configuration files are valid YAML

### No Data in InfluxDB

- Verify Modbus connection to heat pump
- Check heat pump IP address and port in `config/heatpumps.yml`
- Review collector logs for connection errors
- Ensure register addresses in `config/registers.yml` are correct

### Grafana Can't Connect to InfluxDB

- Verify InfluxDB is running: `docker-compose ps influxdb`
- Check InfluxDB token in provisioning configuration
- Ensure network connectivity between containers

## Next Steps

1. Populate `config/registers.yml` with your specific heat pump register mappings
2. Customize polling intervals in `config/collector.yml`
3. Create Grafana dashboards for your specific monitoring needs
4. Set up alerts for temperature or power anomalies
5. Configure InfluxDB retention policies for long-term data management

## Security Notes

- Change default passwords in production
- Use environment variables for sensitive tokens
- Restrict network access to Modbus interfaces
- Enable authentication on Grafana
- Regularly update dependencies

## License

[Add your license here]

## Support

For issues and questions, please refer to the project documentation or create an issue in the repository.
