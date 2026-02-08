"""
Heat pump device abstraction with register mapping and metric collection.
"""

import logging
from typing import Dict, List, Any, Optional
from .modbus_client import ModbusClient


logger = logging.getLogger(__name__)


class RegisterConfig:
    """Configuration for a single Modbus register."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize register configuration from YAML dict.

        Args:
            config: Register configuration dictionary from registers.yml
        """
        self.name = config["name"]
        self.address = config["address"]
        self.register_type = config.get("register_type", "holding")
        self.data_type = config.get("data_type", "int16")
        self.unit = config.get("unit", "")
        self.scale = config.get("scale", 1.0)
        self.description = config.get("description", "")
        self.enum_values = config.get("enum_values", {})
        self.bitmap_fields = config.get("bitmap_fields", {})

    def __repr__(self):
        return f"RegisterConfig(name={self.name}, address={self.address}, type={self.data_type})"


class HeatPump:
    """Represents a single heat pump device with Modbus communication."""

    def __init__(
        self,
        heat_pump_id: str,
        name: str,
        location: str,
        model: str,
        modbus_client: ModbusClient,
        registers: List[RegisterConfig],
    ):
        """
        Initialize heat pump device.

        Args:
            heat_pump_id: Unique identifier for this heat pump
            name: Friendly name
            location: Physical location
            model: Heat pump model name
            modbus_client: Configured ModbusClient instance
            registers: List of register configurations for this model
        """
        self.id = heat_pump_id
        self.name = name
        self.location = location
        self.model = model
        self.client = modbus_client
        self.registers = {reg.name: reg for reg in registers}

        logger.info(
            f"Initialized heat pump '{self.name}' (id={self.id}, "
            f"model={self.model}, location={self.location}) "
            f"with {len(self.registers)} registers"
        )

    async def connect(self) -> bool:
        """
        Connect to heat pump via Modbus.

        Returns:
            True if connection successful, False otherwise
        """
        logger.info(f"Connecting to heat pump '{self.name}' ({self.id})")
        return await self.client.connect()

    async def disconnect(self):
        """Disconnect from heat pump."""
        logger.info(f"Disconnecting from heat pump '{self.name}' ({self.id})")
        await self.client.disconnect()

    async def read_metric(self, metric_name: str) -> Optional[Any]:
        """
        Read a single metric from the heat pump.

        Args:
            metric_name: Name of the metric to read (must match register name)

        Returns:
            Metric value or None on failure
        """
        if metric_name not in self.registers:
            logger.error(
                f"Unknown metric '{metric_name}' for heat pump '{self.name}'. "
                f"Available metrics: {list(self.registers.keys())}"
            )
            return None

        register = self.registers[metric_name]

        try:
            value = await self.client.read_register(
                address=register.address,
                register_type=register.register_type,
                data_type=register.data_type,
                scale=register.scale,
            )

            if value is not None:
                # Handle enum values if defined
                if register.unit == "enum" and register.enum_values:
                    int_value = int(value)
                    enum_str = register.enum_values.get(int_value, f"Unknown({int_value})")
                    logger.debug(
                        f"Read {metric_name} from '{self.name}': {value} -> {enum_str}"
                    )
                    return enum_str
                else:
                    logger.debug(
                        f"Read {metric_name} from '{self.name}': {value} {register.unit}"
                    )
                    return value
            else:
                logger.warning(f"Failed to read {metric_name} from '{self.name}'")
                return None

        except Exception as e:
            logger.error(f"Error reading {metric_name} from '{self.name}': {e}")
            return None

    def decode_bitmap(self, register: RegisterConfig, raw_value: int) -> Dict[str, bool]:
        """
        Decode a bitmap register into individual boolean fields.

        Args:
            register: Register configuration with bitmap_fields
            raw_value: Raw integer value from Modbus

        Returns:
            Dictionary of field_name -> bool
        """
        fields = {}
        for bit_str, field_name in register.bitmap_fields.items():
            bit = int(bit_str)
            fields[field_name] = bool(raw_value & (1 << bit))
        return fields

    async def read_all_metrics(self) -> Dict[str, Any]:
        """
        Read all configured metrics from the heat pump.
        Bitmap registers are expanded into individual boolean fields.

        Returns:
            Dictionary of metric_name -> value for successfully read metrics
        """
        logger.debug(f"Reading all metrics from heat pump '{self.name}' ({self.id})")

        metrics = {}
        failed_count = 0

        for metric_name in self.registers.keys():
            value = await self.read_metric(metric_name)
            if value is not None:
                register = self.registers[metric_name]

                # Expand bitmap registers into individual boolean fields
                if register.unit == "bitmap" and register.bitmap_fields:
                    raw_value = int(value)
                    metrics[metric_name] = raw_value
                    bitmap_fields = self.decode_bitmap(register, raw_value)
                    metrics.update(bitmap_fields)
                    logger.debug(
                        f"Decoded bitmap '{metric_name}' (raw={raw_value}): "
                        f"{[k for k, v in bitmap_fields.items() if v]}"
                    )
                else:
                    metrics[metric_name] = value
            else:
                failed_count += 1

        success_count = len(self.registers) - failed_count
        total_count = len(self.registers)

        if success_count > 0:
            logger.info(
                f"Read {success_count}/{total_count} metrics from '{self.name}' "
                f"({failed_count} failed)"
            )
        else:
            logger.error(f"Failed to read any metrics from '{self.name}'")

        return metrics

    def get_tags(self) -> Dict[str, str]:
        """
        Get InfluxDB tags for this heat pump.

        Returns:
            Dictionary of tag names and values
        """
        return {
            "heat_pump_id": self.id,
            "name": self.name,
            "location": self.location,
            "model": self.model,
        }

    def validate_metrics(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate metric values and remove invalid ones.
        Bitmap-expanded boolean fields are passed through as-is.

        Args:
            metrics: Dictionary of metric_name -> value

        Returns:
            Dictionary with only valid metrics
        """
        valid_metrics = {}

        # Build set of known bitmap field names for pass-through
        bitmap_field_names = set()
        for register in self.registers.values():
            if register.bitmap_fields:
                bitmap_field_names.update(register.bitmap_fields.values())

        for metric_name, value in metrics.items():
            # Bitmap-expanded boolean fields: pass through directly
            if metric_name in bitmap_field_names:
                if isinstance(value, bool):
                    valid_metrics[metric_name] = value
                continue

            if metric_name not in self.registers:
                logger.warning(
                    f"Skipping unknown metric '{metric_name}' for '{self.name}'"
                )
                continue

            register = self.registers[metric_name]

            # Basic validation
            try:
                # Check if value is numeric (for non-enum types)
                if register.unit != "enum" and register.unit != "bitmap":
                    if not isinstance(value, (int, float)):
                        logger.warning(
                            f"Invalid type for {metric_name} in '{self.name}': "
                            f"expected number, got {type(value)}"
                        )
                        continue

                # Add to valid metrics
                valid_metrics[metric_name] = value

            except Exception as e:
                logger.warning(
                    f"Validation error for {metric_name} in '{self.name}': {e}"
                )
                continue

        return valid_metrics

    @property
    def is_connected(self) -> bool:
        """Check if heat pump is connected."""
        return self.client.is_connected

    def __repr__(self):
        return (
            f"HeatPump(id={self.id}, name={self.name}, "
            f"model={self.model}, registers={len(self.registers)})"
        )
