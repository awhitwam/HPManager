"""
InfluxDB writer with batching and error handling.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import ASYNCHRONOUS


logger = logging.getLogger(__name__)


class InfluxWriter:
    """Asynchronous InfluxDB writer with batching support."""

    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
        batch_size: int = 100,
        flush_interval: float = 5.0,
        timeout: int = 10000,
        retry_interval: float = 30.0,
    ):
        """
        Initialize InfluxDB writer.

        Args:
            url: InfluxDB URL (e.g., http://localhost:8086)
            token: Authentication token
            org: Organization name
            bucket: Bucket name
            batch_size: Maximum points per batch
            flush_interval: Seconds between automatic batch flushes
            timeout: Request timeout in milliseconds
            retry_interval: Seconds between reconnection attempts
        """
        self.url = url
        self.token = token
        self.org = org
        self.bucket = bucket
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.timeout = timeout
        self.retry_interval = retry_interval

        self.client: Optional[InfluxDBClient] = None
        self.write_api = None
        self._buffer: List[Point] = []
        self._buffer_lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False
        self._connected = False

        logger.info(
            f"Initialized InfluxWriter for {url} "
            f"(org={org}, bucket={bucket}, batch_size={batch_size})"
        )

    def connect(self) -> bool:
        """
        Connect to InfluxDB.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info(f"Connecting to InfluxDB at {self.url}")

            self.client = InfluxDBClient(
                url=self.url,
                token=self.token,
                org=self.org,
                timeout=self.timeout,
            )

            # Test connection by getting bucket info
            buckets_api = self.client.buckets_api()
            bucket = buckets_api.find_bucket_by_name(self.bucket)

            if bucket:
                logger.info(f"Successfully connected to InfluxDB (bucket: {self.bucket})")
                self.write_api = self.client.write_api(write_options=ASYNCHRONOUS)
                self._connected = True
                return True
            else:
                logger.error(f"Bucket '{self.bucket}' not found")
                self._connected = False
                return False

        except Exception as e:
            logger.error(f"Failed to connect to InfluxDB: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Disconnect from InfluxDB."""
        if self.client:
            logger.info("Disconnecting from InfluxDB")
            if self.write_api:
                self.write_api.close()
            self.client.close()
            self._connected = False

    async def start(self):
        """Start the automatic flush task."""
        if not self._connected:
            if not self.connect():
                logger.error("Cannot start writer: not connected to InfluxDB")
                return

        self._running = True
        self._flush_task = asyncio.create_task(self._auto_flush())
        logger.info(f"Started InfluxWriter with {self.flush_interval}s flush interval")

    async def stop(self):
        """Stop the automatic flush task and flush remaining data."""
        logger.info("Stopping InfluxWriter")
        self._running = False

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Flush remaining data
        await self.flush()
        self.disconnect()

    async def _auto_flush(self):
        """Background task to periodically flush the buffer."""
        while self._running:
            try:
                await asyncio.sleep(self.flush_interval)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in auto-flush task: {e}")

    async def write_metrics(
        self,
        measurement: str,
        tags: Dict[str, str],
        fields: Dict[str, Any],
        timestamp: Optional[datetime] = None,
    ):
        """
        Write metrics to InfluxDB (buffered).

        Args:
            measurement: Measurement name
            tags: Dictionary of tag names and values
            fields: Dictionary of field names and values
            timestamp: Optional timestamp (defaults to current time)
        """
        if not fields:
            logger.warning("Attempted to write metrics with no fields, skipping")
            return

        try:
            # Create point
            point = Point(measurement)

            # Add tags
            for tag_key, tag_value in tags.items():
                point.tag(tag_key, str(tag_value))

            # Add fields
            for field_key, field_value in fields.items():
                if isinstance(field_value, bool):
                    point.field(field_key, field_value)
                elif isinstance(field_value, int):
                    point.field(field_key, field_value)
                elif isinstance(field_value, float):
                    point.field(field_key, field_value)
                elif isinstance(field_value, str):
                    point.field(field_key, field_value)
                else:
                    logger.warning(
                        f"Unsupported field type for {field_key}: {type(field_value)}"
                    )
                    continue

            # Add timestamp
            if timestamp:
                point.time(timestamp)

            # Add to buffer
            async with self._buffer_lock:
                self._buffer.append(point)

                # Flush if buffer is full
                if len(self._buffer) >= self.batch_size:
                    await self._flush_buffer()

            logger.debug(
                f"Buffered metrics for {measurement} "
                f"(buffer size: {len(self._buffer)}/{self.batch_size})"
            )

        except Exception as e:
            logger.error(f"Error creating point for {measurement}: {e}")

    async def _flush_buffer(self):
        """Flush the buffer to InfluxDB (internal, assumes lock is held)."""
        if not self._buffer:
            return

        if not self._connected or not self.write_api:
            logger.warning("Cannot flush: not connected to InfluxDB")
            return

        points_to_write = self._buffer[:]
        point_count = len(points_to_write)

        try:
            logger.debug(f"Flushing {point_count} points to InfluxDB")

            # Write to InfluxDB
            self.write_api.write(
                bucket=self.bucket,
                org=self.org,
                record=points_to_write,
            )

            # Clear buffer on success
            self._buffer.clear()
            logger.info(f"Successfully wrote {point_count} points to InfluxDB")

        except Exception as e:
            logger.error(f"Failed to write {point_count} points to InfluxDB: {e}")

            # Keep points in buffer for retry, but limit buffer size
            if len(self._buffer) > 1000:
                dropped_count = len(self._buffer) - 1000
                self._buffer = self._buffer[-1000:]
                logger.warning(
                    f"Buffer overflow: dropped {dropped_count} oldest points "
                    f"(kept most recent 1000)"
                )

    async def flush(self):
        """Manually flush the buffer to InfluxDB."""
        async with self._buffer_lock:
            if self._buffer:
                logger.info(f"Manual flush of {len(self._buffer)} points")
                await self._flush_buffer()
            else:
                logger.debug("Flush called with empty buffer")

    @property
    def is_connected(self) -> bool:
        """Check if writer is connected to InfluxDB."""
        return self._connected

    @property
    def buffer_size(self) -> int:
        """Get current buffer size."""
        return len(self._buffer)

    def __repr__(self):
        return (
            f"InfluxWriter(url={self.url}, org={self.org}, "
            f"bucket={self.bucket}, connected={self._connected})"
        )
