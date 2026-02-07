# Testing Modbus Connectivity Without Docker

This guide explains how to test the collector's Modbus functionality locally without Docker.

## Prerequisites

1. **Python 3.11+** installed on your system
2. **Virtual environment** (recommended)
3. **Network access** to your heat pump

## Setup

### 1. Create and activate virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python -m venv venv
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure your heat pump

Edit `config/heatpumps.yml` with your heat pump's IP address:

```yaml
heatpumps:
  - id: hp1
    name: My Heat Pump
    location: Basement
    model: your_model_name  # Must match model in registers.yml
    enabled: true
    modbus:
      type: tcp
      host: 192.168.1.100    # ← Your heat pump IP
      port: 502
      unit_id: 1
      timeout: 5
      retries: 3
      retry_delay: 1
```

### 4. Configure registers

Edit `config/registers.yml` with your heat pump's Modbus register mappings:

```yaml
models:
  your_model_name:
    description: "Your heat pump model"
    registers:
      - name: outside_temperature
        address: 100           # ← Your register addresses
        register_type: holding
        data_type: int16
        unit: celsius
        scale: 0.1
        description: "Outside air temperature"
      # Add more registers...
```

## Running Tests

### Test Script Overview

The `test_modbus.py` script provides two modes:

1. **Single Read** - One-time connection and data read (good for initial testing)
2. **Continuous Polling** - Ongoing data collection (good for monitoring)

### Single Read Test

Run a one-time test to verify connectivity and data collection:

```bash
python test_modbus.py
```

The script will:
1. Load your configuration
2. List available heat pumps
3. Connect to the selected heat pump
4. Read all configured registers
5. Display the results in a table

**Example output:**

```
╔════════════════════════════════════════════════════════════════════╗
║          HPManager - Modbus Data Collection Test                  ║
╚════════════════════════════════════════════════════════════════════╝

📁 Loading configuration files...
✅ Configuration loaded

📋 Found 1 enabled heat pump(s):
  1. Main Heat Pump (ID: hp1, Model: stiebel_eltron_wpm)

======================================================================
Testing Heat Pump: Main Heat Pump (ID: hp1)
======================================================================
Location: Basement
Model: stiebel_eltron_wpm
Modbus Type: tcp
IP Address: 192.168.1.100:502
Unit ID: 1

Total Registers to Read: 45
----------------------------------------------------------------------
✅ Modbus client created
✅ Connected to heat pump

📊 Reading data from heat pump...
✅ Successfully read 42 values

Register Name                  Value           Unit       Address
----------------------------------------------------------------------
outside_temperature            12.50           celsius    100
actual_flow_temperature        35.20           celsius    101
actual_return_temperature      30.10           celsius    102
inverter_power                 2.35            kW         103
...

======================================================================
Summary:
  ✅ Successful reads: 42/45
  ❌ Failed reads: 3/45
======================================================================

✅ Disconnected from heat pump
✅ Test completed successfully!
```

### Continuous Polling Test

For ongoing monitoring (useful for debugging):

```bash
python test_modbus.py
# Select mode 2 when prompted
```

This will:
- Poll the heat pump every 10 seconds (configurable in `config/collector.yml`)
- Display key metrics on each poll
- Continue until you press Ctrl+C

**Example output:**

```
🔄 Starting continuous polling (every 10s)
Press Ctrl+C to stop

--- Poll #1 at 14:23:15 ---
  outside_temperature: 12.50
  actual_flow_temperature: 35.20
  actual_return_temperature: 30.10
  inverter_power: 2.35
  Total values: 42

--- Poll #2 at 14:23:25 ---
  outside_temperature: 12.45
  actual_flow_temperature: 35.30
  ...
```

Press `Ctrl+C` to stop:

```
✋ Stopping continuous polling (completed 15 polls)
✅ Disconnected
```

## Troubleshooting

### Connection Failed

**Error:** `❌ Failed to connect: [Errno 111] Connection refused`

**Solutions:**
- Verify the heat pump IP address is correct
- Check network connectivity: `ping 192.168.1.100`
- Ensure the heat pump's Modbus TCP service is enabled
- Check firewall settings

### Timeout Errors

**Error:** `❌ Failed to connect: timed out`

**Solutions:**
- Increase timeout in `config/heatpumps.yml`:
  ```yaml
  modbus:
    timeout: 10  # Increase from 5 to 10 seconds
  ```
- Check network latency
- Verify heat pump is powered on and accessible

### No Data / All Reads Failed

**Error:** `⚠️ No data received (all reads failed)`

**Solutions:**
- Verify register addresses in `config/registers.yml` match your heat pump's documentation
- Check `unit_id` is correct (usually 1)
- Try reading a single known-good register first
- Check `register_type` (holding vs input)

### Register Read Failures

Some registers succeed, others fail:

**Solutions:**
- Some registers may not be available depending on heat pump mode/state
- Check register addresses in your heat pump's Modbus documentation
- Verify `data_type` matches the register (int16, uint16, etc.)

### Import Errors

**Error:** `ModuleNotFoundError: No module named 'pymodbus'`

**Solution:**
```bash
# Make sure virtual environment is activated
pip install -r requirements.txt
```

## Quick Test Checklist

- [ ] Virtual environment activated
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] Heat pump IP address configured in `config/heatpumps.yml`
- [ ] Register addresses configured in `config/registers.yml`
- [ ] Heat pump is powered on and accessible on network
- [ ] Can ping heat pump: `ping <IP_ADDRESS>`

## Next Steps

Once the test succeeds:

1. **Verify Data**: Check that the values make sense for your heat pump
2. **Run Full System**: Use Docker Compose to run the complete system:
   ```bash
   docker-compose up -d
   ```
3. **View Dashboard**: Open http://localhost:8000
4. **Check InfluxDB**: Verify data is being stored
5. **Build Grafana Dashboards**: Create visualizations

## Example Configuration Files

### Minimal `heatpumps.yml`

```yaml
heatpumps:
  - id: hp1
    name: Test Heat Pump
    location: Test
    model: generic
    enabled: true
    modbus:
      type: tcp
      host: 192.168.1.100
      port: 502
      unit_id: 1
      timeout: 5
      retries: 3
      retry_delay: 1
```

### Minimal `registers.yml`

```yaml
models:
  generic:
    description: "Generic test configuration"
    registers:
      - name: test_register
        address: 0  # Try address 0 or 1 first
        register_type: holding
        data_type: int16
        unit: ""
        scale: 1
        description: "Test register"
```

## Advanced: Testing Specific Registers

To test a specific register without running the full test:

```python
from pymodbus.client import ModbusTcpClient

client = ModbusTcpClient('192.168.1.100', port=502)
client.connect()

# Read holding register at address 100
result = client.read_holding_registers(100, 1, slave=1)

if not result.isError():
    print(f"Value: {result.registers[0]}")
else:
    print(f"Error: {result}")

client.close()
```

## Support

If issues persist:

1. Check heat pump manufacturer's Modbus documentation
2. Verify Modbus TCP is enabled on the heat pump
3. Try a Modbus testing tool (e.g., modpoll, QModMaster)
4. Check HPManager logs for detailed error messages
