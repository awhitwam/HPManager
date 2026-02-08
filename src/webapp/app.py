"""
Real-time web dashboard for heat pump monitoring.
Provides at-a-glance view of current heat pump status.
"""

import os
import subprocess
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
import yaml
from influxdb_client import InfluxDBClient
from influxdb_client.client.query_api import QueryApi
from .config_manager import get_config_manager
from .schemas import HeatPumpConfig, AppSettings, DEFAULT_VISIBLE_FIELDS


app = FastAPI(title="HPManager Dashboard", version="1.0.0")

# Templates
templates = Jinja2Templates(directory="/app/src/webapp/templates")

# Configuration
CONFIG_DIR = os.getenv("CONFIG_DIR", "/app/config")
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://influxdb:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "heatpump-monitoring")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "heatpump-data")

# InfluxDB client
influx_client: Optional[InfluxDBClient] = None
query_api: Optional[QueryApi] = None


def init_influxdb():
    """Initialize InfluxDB client."""
    global influx_client, query_api
    try:
        influx_client = InfluxDBClient(
            url=INFLUXDB_URL,
            token=INFLUXDB_TOKEN,
            org=INFLUXDB_ORG,
        )
        query_api = influx_client.query_api()
        print(f"Connected to InfluxDB at {INFLUXDB_URL}")
    except Exception as e:
        print(f"Failed to connect to InfluxDB: {e}")


def load_heatpump_config() -> List[Dict[str, Any]]:
    """Load heat pump configuration from YAML."""
    config_path = os.path.join(CONFIG_DIR, "heatpumps.yml")
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            return config.get("heatpumps", [])
    except Exception as e:
        print(f"Error loading heat pump config: {e}")
        return []


def get_latest_data(heat_pump_id: str) -> Dict[str, Any]:
    """
    Get the latest data for a specific heat pump from InfluxDB.

    Args:
        heat_pump_id: Heat pump identifier

    Returns:
        Dictionary with latest metrics
    """
    if not query_api:
        return {}

    # Query for the last data point
    query = f'''
    from(bucket: "{INFLUXDB_BUCKET}")
        |> range(start: -5m)
        |> filter(fn: (r) => r["_measurement"] == "heatpump_metrics")
        |> filter(fn: (r) => r["heat_pump_id"] == "{heat_pump_id}")
        |> last()
    '''

    try:
        result = query_api.query(query=query)

        # Parse results into a dictionary
        data = {
            "heat_pump_id": heat_pump_id,
            "timestamp": None,
            "metrics": {},
        }

        for table in result:
            for record in table.records:
                field_name = record.get_field()
                value = record.get_value()
                timestamp = record.get_time()

                data["metrics"][field_name] = value
                if timestamp and (not data["timestamp"] or timestamp > data["timestamp"]):
                    data["timestamp"] = timestamp

        return data

    except Exception as e:
        print(f"Error querying InfluxDB for {heat_pump_id}: {e}")
        return {"heat_pump_id": heat_pump_id, "error": str(e)}


def calculate_cop(data: Dict[str, Any]) -> Optional[float]:
    """
    Calculate COP from heat meter and power consumption data.

    Args:
        data: Dictionary containing metrics

    Returns:
        COP value or None if cannot be calculated
    """
    metrics = data.get("metrics", {})

    # Try to calculate instantaneous COP if we have power data
    # This would require thermal power output, which may need to be calculated
    # from flow rate, flow temp, and return temp

    # For now, return None - this can be enhanced later
    return None


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    init_influxdb()


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown."""
    if influx_client:
        influx_client.close()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    heat_pumps = load_heatpump_config()

    # Load display settings
    config_mgr = get_config_manager()
    display_settings = config_mgr.load_display_settings()
    visible_fields_map = display_settings.get("visible_fields", {})
    refresh_interval = display_settings.get("refresh_interval", 10)

    # Get latest data for each heat pump
    heat_pump_data = []
    for hp in heat_pumps:
        if hp.get("enabled", True):
            hp_id = hp["id"]
            latest_data = get_latest_data(hp_id)

            hp_info = {
                "id": hp_id,
                "name": hp["name"],
                "location": hp["location"],
                "model": hp["model"],
                "data": latest_data,
                "cop": calculate_cop(latest_data),
                "visible_fields": visible_fields_map.get(hp_id, list(DEFAULT_VISIBLE_FIELDS)),
            }
            heat_pump_data.append(hp_info)

    sparkline_minutes = display_settings.get("sparkline_minutes", 30)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "heat_pumps": heat_pump_data,
            "last_update": datetime.utcnow(),
            "refresh_interval": refresh_interval,
            "sparkline_minutes": sparkline_minutes,
            "DEFAULT_VISIBLE_FIELDS": DEFAULT_VISIBLE_FIELDS,
        }
    )


@app.get("/api/heatpumps", response_class=JSONResponse)
async def get_all_heatpumps():
    """API endpoint to get current data for all heat pumps."""
    heat_pumps = load_heatpump_config()

    heat_pump_data = []
    for hp in heat_pumps:
        if hp.get("enabled", True):
            hp_id = hp["id"]
            latest_data = get_latest_data(hp_id)

            hp_info = {
                "id": hp_id,
                "name": hp["name"],
                "location": hp["location"],
                "model": hp["model"],
                "enabled": hp.get("enabled", True),
                "modbus": hp.get("modbus", {}),
                "data": latest_data,
                "cop": calculate_cop(latest_data),
            }
            heat_pump_data.append(hp_info)

    return {
        "heat_pumps": heat_pump_data,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/heatpumps/{heat_pump_id}", response_class=JSONResponse)
async def get_heatpump(heat_pump_id: str):
    """API endpoint to get current data for a specific heat pump."""
    heat_pumps = load_heatpump_config()

    # Find the heat pump
    hp = next((h for h in heat_pumps if h["id"] == heat_pump_id), None)

    if not hp:
        return JSONResponse(
            status_code=404,
            content={"error": f"Heat pump {heat_pump_id} not found"}
        )

    latest_data = get_latest_data(heat_pump_id)

    return {
        "id": heat_pump_id,
        "name": hp["name"],
        "location": hp["location"],
        "model": hp["model"],
        "enabled": hp.get("enabled", True),
        "modbus": hp.get("modbus", {}),
        "data": latest_data,
        "cop": calculate_cop(latest_data),
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/models", response_class=JSONResponse)
async def get_models():
    """API endpoint to get available heat pump models."""
    try:
        config_mgr = get_config_manager()
        models = config_mgr.get_model_info()
        return {"models": models}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load models: {str(e)}")


@app.post("/api/heatpumps", response_class=JSONResponse, status_code=201)
async def create_heatpump(heatpump: HeatPumpConfig):
    """API endpoint to create a new heat pump."""
    try:
        config_mgr = get_config_manager()
        config_mgr.create_heatpump(heatpump)

        return {
            "message": "Heat pump created successfully",
            "heat_pump": heatpump.model_dump(),
            "requires_restart": True
        }
    except ValueError as e:
        # ID already exists or validation error
        if "already exists" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create heat pump: {str(e)}")


@app.put("/api/heatpumps/{heat_pump_id}", response_class=JSONResponse)
async def update_heatpump(heat_pump_id: str, heatpump: HeatPumpConfig):
    """API endpoint to update an existing heat pump (full replacement)."""
    try:
        config_mgr = get_config_manager()
        config_mgr.update_heatpump(heat_pump_id, heatpump)

        return {
            "message": "Heat pump updated successfully",
            "heat_pump": heatpump.model_dump(),
            "requires_restart": True
        }
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        if "already exists" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update heat pump: {str(e)}")


@app.patch("/api/heatpumps/{heat_pump_id}", response_class=JSONResponse)
async def patch_heatpump(heat_pump_id: str, updates: Dict[str, Any] = Body(...)):
    """API endpoint to partially update an existing heat pump."""
    try:
        config_mgr = get_config_manager()
        updated_hp = config_mgr.patch_heatpump(heat_pump_id, updates)

        return {
            "message": "Heat pump updated successfully",
            "heat_pump": updated_hp.model_dump(),
            "requires_restart": True
        }
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"Validation error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update heat pump: {str(e)}")


@app.delete("/api/heatpumps/{heat_pump_id}", response_class=JSONResponse)
async def delete_heatpump(heat_pump_id: str):
    """API endpoint to delete a heat pump."""
    try:
        config_mgr = get_config_manager()
        deleted_hp = config_mgr.delete_heatpump(heat_pump_id)

        return {
            "message": f"Heat pump '{deleted_hp.name}' deleted successfully",
            "requires_restart": True
        }
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete heat pump: {str(e)}")


@app.post("/api/collector/restart", response_class=JSONResponse)
async def restart_collector():
    """API endpoint to restart the collector container."""
    try:
        result = subprocess.run(
            ["docker", "restart", "hpm-collector"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30
        )

        return {
            "message": "Collector restarted successfully",
            "status": "restarting"
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Restart operation timed out")
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to restart collector: {e.stderr}"
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="Docker command not found. Ensure Docker is installed in the container."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@app.get("/api/heatpumps/{heat_pump_id}/history", response_class=JSONResponse)
async def get_heatpump_history(heat_pump_id: str, minutes: int = 30):
    """Get historical data for sparkline graphs."""
    if not query_api:
        return {"heat_pump_id": heat_pump_id, "minutes": minutes, "fields": {}}

    # Clamp minutes
    minutes = max(5, min(480, minutes))

    # Calculate aggregation window to get ~60 data points
    agg_seconds = max(30, (minutes * 60) // 60)

    # Non-numeric fields to exclude from history
    skip_fields = {
        "operating_mode", "fault_status", "operating_status",
        "compressor_running", "hc1_pump", "hc2_pump", "heatup_program",
        "nhz_stages_running", "hp_heating_mode", "hp_dhw_mode",
        "summer_mode", "cooling_mode", "defrost_mode",
        "silent_mode_1", "silent_mode_2", "evu_release",
    }

    query = f'''
    from(bucket: "{INFLUXDB_BUCKET}")
        |> range(start: -{minutes}m)
        |> filter(fn: (r) => r["_measurement"] == "heatpump_metrics")
        |> filter(fn: (r) => r["heat_pump_id"] == "{heat_pump_id}")
        |> filter(fn: (r) => r["_field"] !~ /^(operating_mode|fault_status|operating_status|compressor_running|hc1_pump|hc2_pump|heatup_program|nhz_stages_running|hp_heating_mode|hp_dhw_mode|summer_mode|cooling_mode|defrost_mode|silent_mode_1|silent_mode_2|evu_release)$/)
        |> aggregateWindow(every: {agg_seconds}s, fn: mean, createEmpty: false)
        |> yield(name: "mean")
    '''

    try:
        result = query_api.query(query=query)

        fields = {}
        for table in result:
            for record in table.records:
                field_name = record.get_field()
                value = record.get_value()
                timestamp = record.get_time()

                if field_name not in fields:
                    fields[field_name] = []

                if value is not None:
                    fields[field_name].append({
                        "time": timestamp.isoformat(),
                        "value": round(float(value), 2),
                    })

        return {
            "heat_pump_id": heat_pump_id,
            "minutes": minutes,
            "fields": fields,
        }

    except Exception as e:
        print(f"Error querying history for {heat_pump_id}: {e}")
        return {"heat_pump_id": heat_pump_id, "minutes": minutes, "fields": {}, "error": str(e)}


@app.get("/api/heatpumps/{heat_pump_id}/state-changes", response_class=JSONResponse)
async def get_heatpump_state_changes(heat_pump_id: str, hours: int = 24):
    """Get the timestamp of the last state change for each boolean status field.

    Queries InfluxDB for boolean fields over the last N hours and finds
    when each field last transitioned (changed value). Returns the time
    each field entered its current state.
    """
    if not query_api:
        return {"heat_pump_id": heat_pump_id, "fields": {}}

    hours = max(1, min(168, hours))  # 1h to 7 days

    # Boolean fields from the operating_status bitmap
    bool_fields = [
        "compressor_running", "hc1_pump", "hp_heating_mode", "hp_dhw_mode",
        "defrost_mode", "summer_mode", "cooling_mode", "nhz_stages_running",
    ]
    field_regex = "|".join(bool_fields)

    query = f'''
    from(bucket: "{INFLUXDB_BUCKET}")
        |> range(start: -{hours}h)
        |> filter(fn: (r) => r["_measurement"] == "heatpump_metrics")
        |> filter(fn: (r) => r["heat_pump_id"] == "{heat_pump_id}")
        |> filter(fn: (r) => r["_field"] =~ /^({field_regex})$/)
        |> sort(columns: ["_time"], desc: false)
    '''

    try:
        result = query_api.query(query=query)

        # Collect all data points per field, sorted by time ascending
        field_data = {}
        for table in result:
            for record in table.records:
                field_name = record.get_field()
                value = record.get_value()
                timestamp = record.get_time()

                if field_name not in field_data:
                    field_data[field_name] = []

                field_data[field_name].append({
                    "time": timestamp,
                    "value": bool(value),
                })

        # For each field, find when the current state started
        fields = {}
        for field_name, points in field_data.items():
            if not points:
                continue

            current_value = points[-1]["value"]
            since_time = points[0]["time"]  # default: beginning of query range

            # Walk backwards to find the last transition
            for i in range(len(points) - 1, 0, -1):
                if points[i]["value"] != points[i - 1]["value"]:
                    since_time = points[i]["time"]
                    break

            fields[field_name] = {
                "current": current_value,
                "since": since_time.isoformat(),
            }

        return {
            "heat_pump_id": heat_pump_id,
            "fields": fields,
        }

    except Exception as e:
        print(f"Error querying state changes for {heat_pump_id}: {e}")
        return {"heat_pump_id": heat_pump_id, "fields": {}, "error": str(e)}


@app.get("/api/settings", response_class=JSONResponse)
async def get_settings():
    """Get current application settings."""
    try:
        config_mgr = get_config_manager()
        collector_settings = config_mgr.load_collector_settings()
        display_settings = config_mgr.load_display_settings()
        return {
            "collector": {
                "poll_interval": collector_settings.get("poll_interval", 10.0)
            },
            "display": {
                "refresh_interval": display_settings.get("refresh_interval", 10),
                "sparkline_minutes": display_settings.get("sparkline_minutes", 30),
                "visible_fields": display_settings.get("visible_fields", {}),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load settings: {str(e)}")


@app.put("/api/settings", response_class=JSONResponse)
async def update_settings(settings: AppSettings):
    """Update application settings."""
    try:
        config_mgr = get_config_manager()

        # Detect poll interval changes
        current_collector = config_mgr.load_collector_settings()
        old_poll_interval = current_collector.get("poll_interval", 10.0)
        new_poll_interval = settings.collector.poll_interval
        poll_interval_changed = (old_poll_interval != new_poll_interval)

        # Save collector settings if changed
        if poll_interval_changed:
            config_mgr.save_collector_poll_interval(new_poll_interval)

        # Save display settings
        config_mgr.save_display_settings(settings.display.model_dump())

        return {
            "message": "Settings saved successfully",
            "requires_restart": poll_interval_changed,
        }
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"Validation error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {str(e)}")


@app.get("/api/registers/{model_name}", response_class=JSONResponse)
async def get_register_fields(model_name: str):
    """Get available register fields for a heat pump model."""
    try:
        config_mgr = get_config_manager()
        fields = config_mgr.get_register_fields(model_name)
        if not fields:
            raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")
        return {"model": model_name, "fields": fields}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load registers: {str(e)}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    influx_healthy = influx_client is not None

    return {
        "status": "healthy" if influx_healthy else "degraded",
        "influxdb": "connected" if influx_healthy else "disconnected",
        "timestamp": datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
