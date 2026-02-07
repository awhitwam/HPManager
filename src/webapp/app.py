"""
Real-time web dashboard for heat pump monitoring.
Provides at-a-glance view of current heat pump status.
"""

import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import yaml
from influxdb_client import InfluxDBClient
from influxdb_client.client.query_api import QueryApi


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
        "id": hp_id,
        "name": hp["name"],
        "location": hp["location"],
        "model": hp["model"],
        "data": latest_data,
        "cop": calculate_cop(latest_data),
        "timestamp": datetime.utcnow().isoformat(),
    }


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
