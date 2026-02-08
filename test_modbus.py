#!/usr/bin/env python3
"""
Standalone test script for Modbus data collection.
Tests connectivity and data reading without requiring Docker.

Usage:
    python test_modbus.py
"""

import sys
import asyncio
import yaml
import time
from pathlib import Path
from typing import Dict, Any, List

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent))

from src.collector.modbus_client import ModbusClient
from src.collector.heatpump import HeatPump, RegisterConfig


def load_config(config_dir: str = "config") -> tuple:
    """
    Load configuration files.

    Returns:
        Tuple of (heatpumps, registers, collector_config)
    """
    config_path = Path(config_dir)

    # Load heatpumps config
    with open(config_path / "heatpumps.yml", "r") as f:
        heatpumps_config = yaml.safe_load(f)

    # Load registers config
    with open(config_path / "registers.yml", "r") as f:
        registers_config = yaml.safe_load(f)

    # Load collector config (optional)
    collector_config = {}
    collector_file = config_path / "collector.yml"
    if collector_file.exists():
        with open(collector_file, "r") as f:
            collector_config = yaml.safe_load(f)

    return heatpumps_config, registers_config, collector_config


async def test_single_heat_pump(hp_config: Dict[str, Any], registers: List[RegisterConfig]) -> Dict[str, Any]:
    """
    Test a single heat pump connection and data collection.

    Args:
        hp_config: Heat pump configuration
        registers: List of register configurations

    Returns:
        Dictionary of results
    """
    hp_id = hp_config['id']
    hp_name = hp_config['name']
    modbus_config = hp_config['modbus']

    print(f"\n{'='*70}")
    print(f"Testing Heat Pump: {hp_name} (ID: {hp_id})")
    print(f"{'='*70}")
    print(f"Location: {hp_config['location']}")
    print(f"Model: {hp_config['model']}")
    print(f"Modbus Type: {modbus_config['type']}")

    if modbus_config['type'] == 'tcp':
        print(f"IP Address: {modbus_config['host']}:{modbus_config['port']}")
        print(f"Unit ID: {modbus_config['unit_id']}")
    else:
        print(f"Serial Port: {modbus_config['port']}")
        print(f"Baudrate: {modbus_config['baudrate']}")

    print(f"\nTotal Registers to Read: {len(registers)}")
    print(f"{'-'*70}")

    # Create Modbus client
    try:
        client = ModbusClient(
            connection_type=modbus_config['type'],
            host=modbus_config.get('host'),
            port=modbus_config.get('port', 502),
            unit_id=modbus_config.get('unit_id', 1),
            timeout=modbus_config.get('timeout', 5.0),
            retries=modbus_config.get('retries', 3),
            retry_delay=modbus_config.get('retry_delay', 1.0),
        )
        print("  Modbus client created")
    except Exception as e:
        print(f"  Failed to create Modbus client: {e}")
        return {"error": str(e), "success": False}

    # Connect
    try:
        connected = await client.connect()
        if not connected:
            print("  Failed to connect")
            return {"error": "Connection failed", "success": False}
        print("  Connected to heat pump")
    except Exception as e:
        print(f"  Failed to connect: {e}")
        return {"error": str(e), "success": False}

    # Create HeatPump instance
    heat_pump = HeatPump(
        heat_pump_id=hp_id,
        name=hp_name,
        location=hp_config['location'],
        model=hp_config['model'],
        modbus_client=client,
        registers=registers
    )

    # Read data
    print(f"\n  Reading data from heat pump...")
    try:
        data = await heat_pump.read_all_metrics()

        if not data:
            print("  No data received (all reads failed)")
            await client.disconnect()
            return {"success": False, "data": {}, "error": "No data"}

        print(f"  Successfully read data\n")

        # Display results
        print(f"{'Register Name':<30} {'Value':<15} {'Unit':<10} {'Address':<10}")
        print(f"{'-'*70}")

        successful_reads = 0
        failed_reads = 0
        unavailable_reads = 0

        for register in registers:
            reg_name = register.name
            if reg_name in data:
                value = data[reg_name]
                unit = register.unit or ''
                address = register.address

                # Format value based on type
                if isinstance(value, float):
                    value_str = f"{value:.2f}"
                elif isinstance(value, str):
                    value_str = value
                else:
                    value_str = str(value)

                print(f"{reg_name:<30} {value_str:<15} {unit:<10} {address:<10}")

                # Show decoded bitmap fields
                if register.unit == "bitmap" and register.bitmap_fields:
                    raw_val = int(value)
                    active = []
                    inactive = []
                    for bit_str, field_name in register.bitmap_fields.items():
                        bit = int(bit_str)
                        if raw_val & (1 << bit):
                            active.append(field_name)
                        else:
                            inactive.append(field_name)
                    if active:
                        print(f"  {'ACTIVE:':<28} {', '.join(active)}")
                    else:
                        print(f"  {'ACTIVE:':<28} (none - heat pump idle)")

                successful_reads += 1
            else:
                # Check if register returned substitute value (0x8000 = unavailable)
                # by doing a raw read to distinguish from actual read errors
                try:
                    if register.register_type == "holding":
                        raw = await client.read_holding_registers(register.address, 1)
                    else:
                        raw = await client.read_input_registers(register.address, 1)

                    if raw and raw[0] == 32768:
                        print(f"{reg_name:<30} {'N/A':<15} {'(0x8000)':<10} {register.address:<10}")
                        unavailable_reads += 1
                    else:
                        print(f"{reg_name:<30} {'FAILED':<15} {'':<10} {register.address:<10}")
                        failed_reads += 1
                except Exception:
                    print(f"{reg_name:<30} {'FAILED':<15} {'':<10} {register.address:<10}")
                    failed_reads += 1

        print(f"\n{'='*70}")
        print(f"Summary:")
        print(f"  Successful reads: {successful_reads}/{len(registers)}")
        if unavailable_reads > 0:
            print(f"  Unavailable (0x8000): {unavailable_reads}/{len(registers)}")
        if failed_reads > 0:
            print(f"  Failed reads: {failed_reads}/{len(registers)}")
        print(f"{'='*70}")

        # Disconnect
        await client.disconnect()
        print("\n  Disconnected from heat pump")

        return {
            "success": True,
            "data": data,
            "successful_reads": successful_reads,
            "failed_reads": failed_reads,
            "total_registers": len(registers)
        }

    except Exception as e:
        print(f"  Error reading data: {e}")
        await client.disconnect()
        return {"success": False, "error": str(e)}


async def test_continuous(hp_config: Dict[str, Any], registers: List[RegisterConfig], interval: int = 10):
    """
    Continuously test heat pump data collection.

    Args:
        hp_config: Heat pump configuration
        registers: List of register configurations
        interval: Polling interval in seconds
    """
    print(f"\n  Starting continuous polling (every {interval}s)")
    print("Press Ctrl+C to stop\n")

    modbus_config = hp_config['modbus']

    # Create client and heat pump
    client = ModbusClient(
        connection_type=modbus_config['type'],
        host=modbus_config.get('host'),
        port=modbus_config.get('port', 502),
        unit_id=modbus_config.get('unit_id', 1),
        timeout=modbus_config.get('timeout', 5.0),
        retries=modbus_config.get('retries', 3),
        retry_delay=modbus_config.get('retry_delay', 1.0),
    )
    await client.connect()

    heat_pump = HeatPump(
        heat_pump_id=hp_config['id'],
        name=hp_config['name'],
        location=hp_config['location'],
        model=hp_config['model'],
        modbus_client=client,
        registers=registers
    )

    poll_count = 0

    try:
        while True:
            poll_count += 1
            print(f"\n--- Poll #{poll_count} at {time.strftime('%H:%M:%S')} ---")

            data = await heat_pump.read_all_metrics()

            if data:
                # Display key metrics
                key_metrics = [
                    'outside_temperature',
                    'actual_return_temperature',
                    'hp_flow_temperature',
                    'inverter_power',
                    'actual_dhw_temperature',
                    'operating_mode',
                ]

                for metric in key_metrics:
                    if metric in data:
                        value = data[metric]
                        if isinstance(value, float):
                            print(f"  {metric}: {value:.2f}")
                        else:
                            print(f"  {metric}: {value}")

                # Show active operating status flags
                status_flags = [
                    'compressor_running', 'hp_heating_mode', 'hp_dhw_mode',
                    'hc1_pump', 'defrost_mode', 'summer_mode',
                ]
                active = [f for f in status_flags if data.get(f)]
                if active:
                    print(f"  status: {', '.join(active)}")
                else:
                    print(f"  status: idle")

                print(f"  Total values: {len(data)}")
            else:
                print("  No data received")

            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n  Stopping continuous polling (completed {poll_count} polls)")
        await client.disconnect()
        print("  Disconnected")


def main():
    """Main test function."""
    print("=" * 70)
    print("  HPManager - Modbus Data Collection Test")
    print("=" * 70)

    # Load configuration
    print("\n  Loading configuration files...")
    try:
        heatpumps_config, registers_config, collector_config = load_config()
        print("  Configuration loaded")
    except Exception as e:
        print(f"  Failed to load configuration: {e}")
        sys.exit(1)

    # Get list of heat pumps
    heat_pumps = heatpumps_config.get('heatpumps', [])
    if not heat_pumps:
        print("  No heat pumps configured in config/heatpumps.yml")
        sys.exit(1)

    # Filter enabled heat pumps
    enabled_hps = [hp for hp in heat_pumps if hp.get('enabled', True)]

    if not enabled_hps:
        print("  No enabled heat pumps found")
        sys.exit(1)

    print(f"\n  Found {len(enabled_hps)} enabled heat pump(s):")
    for i, hp in enumerate(enabled_hps, 1):
        print(f"  {i}. {hp['name']} (ID: {hp['id']}, Model: {hp['model']})")

    # Select heat pump to test
    if len(enabled_hps) == 1:
        selected_hp = enabled_hps[0]
        print(f"\n  Testing the only configured heat pump: {selected_hp['name']}")
    else:
        print("\n  Select heat pump to test:")
        for i, hp in enumerate(enabled_hps, 1):
            print(f"  {i}. {hp['name']}")

        while True:
            try:
                choice = input(f"\nEnter number (1-{len(enabled_hps)}): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(enabled_hps):
                    selected_hp = enabled_hps[idx]
                    break
                else:
                    print(f"Please enter a number between 1 and {len(enabled_hps)}")
            except (ValueError, KeyboardInterrupt):
                print("\n\n  Test cancelled")
                sys.exit(0)

    # Get registers for this model
    model = selected_hp['model']
    models = registers_config.get('models', {})

    if model not in models:
        print(f"  Model '{model}' not found in registers.yml")
        sys.exit(1)

    model_config = models[model]
    register_list = model_config.get('registers', [])

    if not register_list:
        print(f"  No registers defined for model '{model}'")
        sys.exit(1)

    # Convert to RegisterConfig objects
    registers = [RegisterConfig(reg) for reg in register_list]

    # Ask for test mode
    print("\n  Test Mode:")
    print("  1. Single read (one-time test)")
    print("  2. Continuous polling (press Ctrl+C to stop)")

    while True:
        try:
            mode = input("\nSelect mode (1 or 2): ").strip()
            if mode in ['1', '2']:
                break
            print("Please enter 1 or 2")
        except KeyboardInterrupt:
            print("\n\n  Test cancelled")
            sys.exit(0)

    # Run test
    if mode == '1':
        result = asyncio.run(test_single_heat_pump(selected_hp, registers))

        if result.get('success'):
            print("\n  Test completed successfully!")
            sys.exit(0)
        else:
            print("\n  Test failed")
            sys.exit(1)
    else:
        # Continuous mode
        interval = collector_config.get('collector', {}).get('poll_interval', 10)
        print(f"\nUsing poll interval: {interval}s (from collector.yml)")

        try:
            asyncio.run(test_continuous(selected_hp, registers, interval))
        except KeyboardInterrupt:
            print("\n  Test stopped")
        except Exception as e:
            print(f"\n  Error: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
