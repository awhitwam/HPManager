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
from .schemas import HeatPumpConfig


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
            }
            heat_pump_data.append(hp_info)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "heat_pumps": heat_pump_data,
            "last_update": datetime.utcnow(),
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
