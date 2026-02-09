"""
Pydantic schemas for heat pump configuration validation.
"""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, IPvAnyAddress
import re


# Default fields shown on dashboard (matches original hardcoded display)
DEFAULT_VISIBLE_FIELDS = [
    "outside_temperature",
    "actual_temperature_hk1",
    "actual_return_temperature",
    "actual_dhw_temperature",
    "inverter_power",
    "operating_status",
    "fault_status",
]


class DisplaySettings(BaseModel):
    """Display configuration for the web dashboard."""
    refresh_interval: int = Field(
        default=10, ge=5, le=120,
        description="Dashboard auto-refresh interval in seconds"
    )
    sparkline_minutes: int = Field(
        default=30, ge=5, le=480,
        description="Time window for sparkline graphs in minutes"
    )
    visible_fields: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Per-pump visible fields: {pump_id: [field_names]}"
    )
    card_order: List[str] = Field(
        default_factory=list,
        description="Order of heat pump cards on dashboard"
    )


class CollectorSettings(BaseModel):
    """Collector configuration settable from the UI."""
    poll_interval: float = Field(
        default=10.0, ge=5.0, le=120.0,
        description="Seconds between polling cycles"
    )


class AppSettings(BaseModel):
    """Combined application settings."""
    collector: CollectorSettings = Field(default_factory=CollectorSettings)
    display: DisplaySettings = Field(default_factory=DisplaySettings)


class ModbusConfigTCP(BaseModel):
    """Modbus TCP configuration."""
    type: Literal["tcp"] = "tcp"
    host: str = Field(..., description="IP address or hostname")
    port: int = Field(default=502, ge=1, le=65535, description="Modbus TCP port")
    unit_id: int = Field(default=1, ge=1, le=247, description="Modbus unit/slave ID")
    timeout: float = Field(default=5.0, gt=0, description="Connection timeout in seconds")
    retries: int = Field(default=3, ge=0, description="Number of retry attempts")
    retry_delay: float = Field(default=1.0, gt=0, description="Delay between retries in seconds")

    @field_validator('host')
    @classmethod
    def validate_host(cls, v: str) -> str:
        """Validate that host is a valid IPv4 address or hostname."""
        # Try to parse as IP address
        try:
            from ipaddress import ip_address
            ip_address(v)
            return v
        except ValueError:
            pass

        # If not an IP, validate as hostname
        # Hostname rules: alphanumeric, hyphens, dots, max 253 chars
        if not v or len(v) > 253:
            raise ValueError("Invalid hostname: must be 1-253 characters")

        # Basic hostname validation
        hostname_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-\.]{0,251}[a-zA-Z0-9])?$'
        if not re.match(hostname_pattern, v):
            raise ValueError("Invalid hostname format")

        return v


class HeatPumpConfig(BaseModel):
    """Heat pump configuration."""
    id: str = Field(..., description="Unique heat pump identifier")
    name: str = Field(..., min_length=1, max_length=100, description="Display name")
    location: str = Field(..., min_length=1, max_length=100, description="Physical location")
    model: str = Field(..., description="Heat pump model (must exist in registers.yml)")
    enabled: bool = Field(default=True, description="Whether heat pump is enabled")
    modbus: ModbusConfigTCP = Field(..., description="Modbus connection configuration")

    @field_validator('id')
    @classmethod
    def validate_id(cls, v: str) -> str:
        """Validate ID: lowercase alphanumeric with underscore/hyphen only."""
        if not v:
            raise ValueError("ID cannot be empty")

        # Convert to lowercase
        v = v.lower()

        # Check format: alphanumeric, underscore, hyphen only
        if not re.match(r'^[a-z0-9_-]+$', v):
            raise ValueError("ID must contain only lowercase letters, numbers, underscores, and hyphens")

        # Must start with letter or number
        if not v[0].isalnum():
            raise ValueError("ID must start with a letter or number")

        return v

    @field_validator('model')
    @classmethod
    def validate_model_format(cls, v: str) -> str:
        """Basic format validation for model name."""
        if not v or not v.strip():
            raise ValueError("Model cannot be empty")
        return v.strip()


class HeatPumpsConfig(BaseModel):
    """Root configuration containing all heat pumps."""
    heatpumps: List[HeatPumpConfig] = Field(default_factory=list, description="List of heat pumps")

    def get_by_id(self, heat_pump_id: str) -> Optional[HeatPumpConfig]:
        """Get heat pump by ID."""
        for hp in self.heatpumps:
            if hp.id == heat_pump_id:
                return hp
        return None

    def id_exists(self, heat_pump_id: str) -> bool:
        """Check if a heat pump ID already exists."""
        return any(hp.id == heat_pump_id for hp in self.heatpumps)

    def add_heatpump(self, heatpump: HeatPumpConfig) -> None:
        """Add a heat pump to the configuration."""
        if self.id_exists(heatpump.id):
            raise ValueError(f"Heat pump with ID '{heatpump.id}' already exists")
        self.heatpumps.append(heatpump)

    def update_heatpump(self, heat_pump_id: str, heatpump: HeatPumpConfig) -> None:
        """Update an existing heat pump."""
        for i, hp in enumerate(self.heatpumps):
            if hp.id == heat_pump_id:
                # If ID is being changed, check for conflicts
                if heatpump.id != heat_pump_id and self.id_exists(heatpump.id):
                    raise ValueError(f"Heat pump with ID '{heatpump.id}' already exists")
                self.heatpumps[i] = heatpump
                return
        raise ValueError(f"Heat pump with ID '{heat_pump_id}' not found")

    def delete_heatpump(self, heat_pump_id: str) -> HeatPumpConfig:
        """Delete a heat pump and return it."""
        for i, hp in enumerate(self.heatpumps):
            if hp.id == heat_pump_id:
                return self.heatpumps.pop(i)
        raise ValueError(f"Heat pump with ID '{heat_pump_id}' not found")
