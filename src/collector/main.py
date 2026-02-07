"""
Main entry point for the heat pump data collector.
Orchestrates polling, data collection, and writing to InfluxDB.
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import List, Dict, Any
import yaml
from dotenv import load_dotenv

from .modbus_client import ModbusClient
from .heatpump import HeatPump, RegisterConfig
from .influx_writer import InfluxWriter


# Load environment variables
load_dotenv()

# Configure logging
def setup_logging(level: str = "INFO", log_format: str = "text"):
    """Configure logging based on settings."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    if log_format == "json":
        # JSON logging format
        import json
        import time

        class JsonFormatter(logging.Formatter):
            def format(self, record):
                log_data = {
                    "timestamp": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)
                    ),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
                if hasattr(record, "heat_pump_id"):
                    log_data["heat_pump_id"] = record.heat_pump_id
                if hasattr(record, "duration_ms"):
                    log_data["duration_ms"] = record.duration_ms
                return json.dumps(log_data)

        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logging.root.handlers = [handler]
    else:
        # Text logging format
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    logging.root.setLevel(log_level)


logger = logging.getLogger(__name__)


class HeatPumpCollector:
    """Main collector class that orchestrates data collection."""

    def __init__(
        self,
        config_dir: Path = Path("/app/config"),
    ):
        """
        Initialize the collector.

        Args:
            config_dir: Directory containing configuration files
        """
        self.config_dir = config_dir
        self.heat_pumps: List[HeatPump] = []
        self.influx_writer: InfluxWriter = None
        self.poll_interval: float = 10.0
        self._running = False
        self._shutdown_event = asyncio.Event()

        logger.info(f"Initializing HeatPumpCollector (config_dir={config_dir})")

    def load_config(self):
        """Load configuration from YAML files."""
        logger.info("Loading configuration files")

        # Load collector configuration
        collector_config_path = self.config_dir / "collector.yml"
        with open(collector_config_path, "r") as f:
            collector_config = yaml.safe_load(f)

        # Set up logging
        log_config = collector_config.get("logging", {})
        setup_logging(
            level=log_config.get("level", "INFO"),
            log_format=log_config.get("format", "text"),
        )

        # Configure polling
        self.poll_interval = collector_config.get("collector", {}).get(
            "poll_interval", 10.0
        )

        # Load register definitions
        registers_config_path = self.config_dir / "registers.yml"
        with open(registers_config_path, "r") as f:
            registers_config = yaml.safe_load(f)

        # Load heat pump configurations
        heatpumps_config_path = self.config_dir / "heatpumps.yml"
        with open(heatpumps_config_path, "r") as f:
            heatpumps_config = yaml.safe_load(f)

        # Initialize InfluxDB writer
        influx_config = collector_config.get("influxdb", {})
        self.influx_writer = InfluxWriter(
            url=os.getenv("INFLUXDB_URL", influx_config.get("url")),
            token=os.getenv("INFLUXDB_TOKEN", influx_config.get("token")),
            org=os.getenv("INFLUXDB_ORG", influx_config.get("org")),
            bucket=os.getenv("INFLUXDB_BUCKET", influx_config.get("bucket")),
            batch_size=collector_config.get("collector", {}).get("batch_size", 100),
            flush_interval=collector_config.get("collector", {}).get(
                "batch_interval", 5.0
            ),
            timeout=influx_config.get("timeout", 10) * 1000,  # Convert to ms
            retry_interval=influx_config.get("retry_interval", 30.0),
        )

        # Initialize heat pumps
        for hp_config in heatpumps_config.get("heatpumps", []):
            if not hp_config.get("enabled", True):
                logger.info(f"Skipping disabled heat pump: {hp_config.get('id')}")
                continue

            self._create_heat_pump(hp_config, registers_config)

        logger.info(
            f"Loaded configuration: {len(self.heat_pumps)} heat pumps, "
            f"poll_interval={self.poll_interval}s"
        )

    def _create_heat_pump(
        self,
        hp_config: Dict[str, Any],
        registers_config: Dict[str, Any],
    ):
        """Create and configure a heat pump instance."""
        hp_id = hp_config["id"]
        model = hp_config["model"]
        modbus_config = hp_config["modbus"]

        # Get register definitions for this model
        model_registers = registers_config.get("models", {}).get(model)
        if not model_registers:
            logger.error(f"No register configuration found for model '{model}'")
            return

        registers = [
            RegisterConfig(reg_config)
            for reg_config in model_registers.get("registers", [])
        ]

        if not registers:
            logger.warning(f"No registers defined for model '{model}'")
            return

        # Create Modbus client
        connection_type = modbus_config.get("type", "tcp")

        if connection_type == "tcp":
            modbus_client = ModbusClient(
                connection_type="tcp",
                host=modbus_config["host"],
                port=modbus_config.get("port", 502),
                unit_id=modbus_config.get("unit_id", 1),
                timeout=modbus_config.get("timeout", 5.0),
                retries=modbus_config.get("retries", 3),
                retry_delay=modbus_config.get("retry_delay", 1.0),
            )
        elif connection_type == "rtu":
            modbus_client = ModbusClient(
                connection_type="rtu",
                serial_port=modbus_config["port"],
                baudrate=modbus_config.get("baudrate", 9600),
                bytesize=modbus_config.get("bytesize", 8),
                parity=modbus_config.get("parity", "N"),
                stopbits=modbus_config.get("stopbits", 1),
                unit_id=modbus_config.get("unit_id", 1),
                timeout=modbus_config.get("timeout", 5.0),
                retries=modbus_config.get("retries", 3),
                retry_delay=modbus_config.get("retry_delay", 1.0),
            )
        else:
            logger.error(f"Invalid connection type '{connection_type}' for {hp_id}")
            return

        # Create heat pump
        heat_pump = HeatPump(
            heat_pump_id=hp_id,
            name=hp_config["name"],
            location=hp_config["location"],
            model=model,
            modbus_client=modbus_client,
            registers=registers,
        )

        self.heat_pumps.append(heat_pump)
        logger.info(f"Created heat pump: {heat_pump}")

    async def poll_heat_pump(self, heat_pump: HeatPump):
        """
        Poll a single heat pump and write metrics to InfluxDB.

        Args:
            heat_pump: HeatPump instance to poll
        """
        import time

        start_time = time.time()

        try:
            # Read all metrics
            metrics = await heat_pump.read_all_metrics()

            if not metrics:
                logger.warning(f"No metrics read from heat pump '{heat_pump.name}'")
                return

            # Validate metrics
            valid_metrics = heat_pump.validate_metrics(metrics)

            if not valid_metrics:
                logger.warning(
                    f"No valid metrics from heat pump '{heat_pump.name}'"
                )
                return

            # Write to InfluxDB
            await self.influx_writer.write_metrics(
                measurement="heatpump_metrics",
                tags=heat_pump.get_tags(),
                fields=valid_metrics,
            )

            duration_ms = int((time.time() - start_time) * 1000)
            logger.info(
                f"Polled '{heat_pump.name}': {len(valid_metrics)} metrics "
                f"in {duration_ms}ms"
            )

        except Exception as e:
            logger.error(f"Error polling heat pump '{heat_pump.name}': {e}")

    async def poll_all_heat_pumps(self):
        """Poll all heat pumps concurrently."""
        if not self.heat_pumps:
            logger.warning("No heat pumps to poll")
            return

        tasks = [self.poll_heat_pump(hp) for hp in self.heat_pumps]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self):
        """Main run loop."""
        logger.info("Starting HeatPumpCollector")

        # Connect to InfluxDB
        if not self.influx_writer.connect():
            logger.error("Failed to connect to InfluxDB, exiting")
            return

        # Start InfluxDB writer
        await self.influx_writer.start()

        # Connect to all heat pumps
        logger.info(f"Connecting to {len(self.heat_pumps)} heat pumps")
        connect_tasks = [hp.connect() for hp in self.heat_pumps]
        results = await asyncio.gather(*connect_tasks, return_exceptions=True)

        connected_count = sum(1 for r in results if r is True)
        logger.info(f"Connected to {connected_count}/{len(self.heat_pumps)} heat pumps")

        if connected_count == 0:
            logger.error("Failed to connect to any heat pumps, exiting")
            await self.influx_writer.stop()
            return

        # Main polling loop
        self._running = True
        logger.info(f"Starting polling loop (interval={self.poll_interval}s)")

        try:
            while self._running:
                await self.poll_all_heat_pumps()

                # Wait for next poll or shutdown
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.poll_interval,
                    )
                    # Shutdown event was set
                    break
                except asyncio.TimeoutError:
                    # Normal timeout, continue polling
                    pass

        except asyncio.CancelledError:
            logger.info("Polling loop cancelled")
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down HeatPumpCollector")
        self._running = False

        # Stop InfluxDB writer (flushes remaining data)
        await self.influx_writer.stop()

        # Disconnect from heat pumps
        disconnect_tasks = [hp.disconnect() for hp in self.heat_pumps]
        await asyncio.gather(*disconnect_tasks, return_exceptions=True)

        logger.info("Shutdown complete")

    def handle_signal(self, sig):
        """Handle shutdown signals."""
        logger.info(f"Received signal {sig}, initiating shutdown")
        self._shutdown_event.set()


async def main():
    """Main entry point."""
    # Determine config directory
    if len(sys.argv) > 1:
        config_dir = Path(sys.argv[1])
    else:
        config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))

    logger.info(f"HPManager Heat Pump Collector starting (config_dir={config_dir})")

    # Create collector
    collector = HeatPumpCollector(config_dir=config_dir)

    # Load configuration
    try:
        collector.load_config()
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    # Set up signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: collector.handle_signal(s))

    # Run collector
    try:
        await collector.run()
    except Exception as e:
        logger.error(f"Collector error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
