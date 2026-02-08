"""
Configuration manager for heat pump YAML files.
Handles reading, writing, and validating heat pump configurations.
"""
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from .schemas import HeatPumpConfig, HeatPumpsConfig, ModbusConfigTCP, DEFAULT_VISIBLE_FIELDS


class ConfigManager:
    """Manages heat pump configuration files."""

    def __init__(self, config_dir: str = "/app/config"):
        """
        Initialize config manager.

        Args:
            config_dir: Path to configuration directory
        """
        self.config_dir = Path(config_dir)
        self.heatpumps_file = self.config_dir / "heatpumps.yml"
        self.registers_file = self.config_dir / "registers.yml"
        self.collector_file = self.config_dir / "collector.yml"
        self.display_file = self.config_dir / "display.yml"
        self.yaml = YAML()
        self.yaml.preserve_quotes = True
        self.yaml.default_flow_style = False
        self.yaml.width = 4096  # Prevent line wrapping

    def load_heatpumps(self) -> HeatPumpsConfig:
        """
        Load and validate heat pump configuration.

        Returns:
            HeatPumpsConfig: Validated configuration

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If configuration is invalid
        """
        if not self.heatpumps_file.exists():
            # Return empty config if file doesn't exist
            return HeatPumpsConfig(heatpumps=[])

        with open(self.heatpumps_file, 'r') as f:
            data = self.yaml.load(f)

        if not data:
            return HeatPumpsConfig(heatpumps=[])

        # Validate against Pydantic model
        try:
            return HeatPumpsConfig(**data)
        except Exception as e:
            raise ValueError(f"Invalid heat pump configuration: {e}")

    def load_raw_heatpumps(self) -> CommentedMap:
        """
        Load raw YAML data (preserves comments and structure).

        Returns:
            CommentedMap: Raw YAML data
        """
        if not self.heatpumps_file.exists():
            return CommentedMap({"heatpumps": []})

        with open(self.heatpumps_file, 'r') as f:
            data = self.yaml.load(f)

        return data if data else CommentedMap({"heatpumps": []})

    def save_heatpumps(self, config: HeatPumpsConfig) -> None:
        """
        Save heat pump configuration with atomic write.

        Args:
            config: Configuration to save

        Raises:
            ValueError: If validation fails
            IOError: If file write fails
        """
        # Validate all models exist
        available_models = self.get_available_models()
        for hp in config.heatpumps:
            if hp.model not in available_models:
                raise ValueError(
                    f"Model '{hp.model}' not found in registers.yml. "
                    f"Available models: {', '.join(available_models)}"
                )

        # Convert to dict for YAML
        data = {"heatpumps": [hp.model_dump() for hp in config.heatpumps]}

        # Atomic write: write to temp file then rename
        temp_file = self.heatpumps_file.with_suffix('.tmp')
        try:
            with open(temp_file, 'w') as f:
                self.yaml.dump(data, f)

            # Atomic rename
            temp_file.replace(self.heatpumps_file)

        except Exception as e:
            # Clean up temp file if it exists
            if temp_file.exists():
                temp_file.unlink()
            raise IOError(f"Failed to write configuration: {e}")

    def get_available_models(self) -> List[str]:
        """
        Get list of available heat pump models from registers.yml.

        Returns:
            List of model names
        """
        if not self.registers_file.exists():
            return []

        try:
            with open(self.registers_file, 'r') as f:
                data = self.yaml.load(f)

            if not data or 'models' not in data:
                return []

            return list(data['models'].keys())

        except Exception:
            return []

    def get_model_info(self) -> List[Dict[str, Any]]:
        """
        Get detailed information about available models.

        Returns:
            List of model info dicts with name, description, register_count
        """
        if not self.registers_file.exists():
            return []

        try:
            with open(self.registers_file, 'r') as f:
                data = self.yaml.load(f)

            if not data or 'models' not in data:
                return []

            models = []
            for model_name, model_data in data['models'].items():
                info = {
                    "name": model_name,
                    "description": model_data.get('description', ''),
                    "register_count": len(model_data.get('registers', []))
                }
                models.append(info)

            return models

        except Exception:
            return []

    def get_heatpump(self, heat_pump_id: str) -> Optional[HeatPumpConfig]:
        """
        Get a single heat pump by ID.

        Args:
            heat_pump_id: Heat pump ID

        Returns:
            HeatPumpConfig if found, None otherwise
        """
        config = self.load_heatpumps()
        return config.get_by_id(heat_pump_id)

    def create_heatpump(self, heatpump: HeatPumpConfig) -> None:
        """
        Create a new heat pump.

        Args:
            heatpump: Heat pump configuration

        Raises:
            ValueError: If ID already exists or validation fails
        """
        config = self.load_heatpumps()
        config.add_heatpump(heatpump)
        self.save_heatpumps(config)

    def update_heatpump(self, heat_pump_id: str, heatpump: HeatPumpConfig) -> None:
        """
        Update an existing heat pump.

        Args:
            heat_pump_id: Current heat pump ID
            heatpump: New heat pump configuration

        Raises:
            ValueError: If heat pump not found or validation fails
        """
        config = self.load_heatpumps()
        config.update_heatpump(heat_pump_id, heatpump)
        self.save_heatpumps(config)

    def patch_heatpump(self, heat_pump_id: str, updates: Dict[str, Any]) -> HeatPumpConfig:
        """
        Partially update a heat pump.

        Args:
            heat_pump_id: Heat pump ID
            updates: Dictionary of fields to update

        Returns:
            Updated HeatPumpConfig

        Raises:
            ValueError: If heat pump not found or validation fails
        """
        config = self.load_heatpumps()
        heatpump = config.get_by_id(heat_pump_id)

        if not heatpump:
            raise ValueError(f"Heat pump with ID '{heat_pump_id}' not found")

        # Convert to dict, apply updates, validate
        hp_dict = heatpump.model_dump()

        # Handle nested modbus config
        if 'modbus' in updates:
            hp_dict['modbus'].update(updates['modbus'])
            del updates['modbus']

        hp_dict.update(updates)

        # Validate and create new config
        updated_hp = HeatPumpConfig(**hp_dict)

        # Update in config
        config.update_heatpump(heat_pump_id, updated_hp)
        self.save_heatpumps(config)

        return updated_hp

    def delete_heatpump(self, heat_pump_id: str) -> HeatPumpConfig:
        """
        Delete a heat pump.

        Args:
            heat_pump_id: Heat pump ID

        Returns:
            Deleted HeatPumpConfig

        Raises:
            ValueError: If heat pump not found
        """
        config = self.load_heatpumps()
        deleted_hp = config.delete_heatpump(heat_pump_id)
        self.save_heatpumps(config)
        return deleted_hp

    # --- Settings management ---

    def load_collector_settings(self) -> dict:
        """Load the collector section from collector.yml."""
        if not self.collector_file.exists():
            return {"poll_interval": 10.0}
        with open(self.collector_file, 'r') as f:
            data = self.yaml.load(f)
        if not data:
            return {"poll_interval": 10.0}
        return dict(data.get("collector", {"poll_interval": 10.0}))

    def save_collector_poll_interval(self, poll_interval: float) -> None:
        """Update poll_interval in collector.yml while preserving other settings."""
        if self.collector_file.exists():
            with open(self.collector_file, 'r') as f:
                data = self.yaml.load(f)
        else:
            data = CommentedMap()

        if not data:
            data = CommentedMap()
        if "collector" not in data:
            data["collector"] = CommentedMap()
        # Write as int if it's a whole number, for cleaner YAML
        data["collector"]["poll_interval"] = int(poll_interval) if poll_interval == int(poll_interval) else poll_interval

        temp_file = self.collector_file.with_suffix('.tmp')
        try:
            with open(temp_file, 'w') as f:
                self.yaml.dump(data, f)
            temp_file.replace(self.collector_file)
        except Exception as e:
            if temp_file.exists():
                temp_file.unlink()
            raise IOError(f"Failed to write collector config: {e}")

    def load_display_settings(self) -> dict:
        """Load display settings from display.yml.

        visible_fields is a dict keyed by heat pump ID.
        Backward compatible: if visible_fields is a flat list (old format),
        it's treated as defaults for all pumps.
        """
        if not self.display_file.exists():
            return {
                "refresh_interval": 10,
                "visible_fields": {},
            }
        with open(self.display_file, 'r') as f:
            data = self.yaml.load(f)
        if not data:
            return {
                "refresh_interval": 10,
                "visible_fields": {},
            }
        result = dict(data)
        if "refresh_interval" not in result:
            result["refresh_interval"] = 10
        if "sparkline_minutes" not in result:
            result["sparkline_minutes"] = 30

        vf = result.get("visible_fields", {})
        if isinstance(vf, list):
            # Old format: flat list -> migrate to empty dict (use as default)
            result["visible_fields"] = {}
        elif isinstance(vf, dict):
            # Convert ruamel CommentedMap to plain dict of lists
            result["visible_fields"] = {str(k): list(v) for k, v in vf.items()}
        else:
            result["visible_fields"] = {}

        return result

    def get_visible_fields_for_pump(self, hp_id: str) -> list:
        """Get visible fields for a specific heat pump, falling back to defaults."""
        settings = self.load_display_settings()
        vf = settings.get("visible_fields", {})
        return vf.get(hp_id, list(DEFAULT_VISIBLE_FIELDS))

    def save_display_settings(self, settings: dict) -> None:
        """Save display settings to display.yml with atomic write."""
        temp_file = self.display_file.with_suffix('.tmp')
        try:
            with open(temp_file, 'w') as f:
                self.yaml.dump(settings, f)
            temp_file.replace(self.display_file)
        except Exception as e:
            if temp_file.exists():
                temp_file.unlink()
            raise IOError(f"Failed to write display config: {e}")

    def get_register_fields(self, model_name: str) -> list:
        """Get register field definitions for a model, for the settings checklist."""
        if not self.registers_file.exists():
            return []
        try:
            with open(self.registers_file, 'r') as f:
                data = self.yaml.load(f)
            model_data = data.get("models", {}).get(model_name)
            if not model_data:
                return []
            fields = []
            for reg in model_data.get("registers", []):
                unit = reg.get("unit", "")
                if unit == "celsius":
                    category = "Temperatures"
                elif unit == "bar":
                    category = "Pressures"
                elif unit in ("kw", "kwh"):
                    category = "Energy"
                elif unit == "l/min":
                    category = "Flow"
                elif unit in ("enum", "bitmap"):
                    category = "Status"
                else:
                    category = "Configuration"
                fields.append({
                    "name": reg["name"],
                    "description": reg.get("description", reg["name"]),
                    "unit": unit,
                    "category": category,
                })
            return fields
        except Exception:
            return []


# Global instance (will be initialized by app.py)
config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """Get the global config manager instance."""
    global config_manager
    if config_manager is None:
        config_dir = os.getenv("CONFIG_DIR", "/app/config")
        config_manager = ConfigManager(config_dir)
    return config_manager
