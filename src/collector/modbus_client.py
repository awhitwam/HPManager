"""
Modbus client wrapper with retry logic and data type conversion.
Supports both TCP and RTU connections.
"""

import asyncio
import struct
import logging
from typing import Optional, Union, Literal
from pymodbus.client import AsyncModbusTcpClient, AsyncModbusSerialClient
from pymodbus.exceptions import ModbusException
from pymodbus.framer import ModbusSocketFramer, ModbusRtuFramer


logger = logging.getLogger(__name__)


class ModbusClient:
    """Asynchronous Modbus client with automatic retry and data type conversion."""

    def __init__(
        self,
        connection_type: Literal["tcp", "rtu"],
        host: Optional[str] = None,
        port: int = 502,
        unit_id: int = 1,
        timeout: float = 5.0,
        retries: int = 3,
        retry_delay: float = 1.0,
        # RTU-specific parameters
        serial_port: Optional[str] = None,
        baudrate: int = 9600,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int = 1,
    ):
        """
        Initialize Modbus client.

        Args:
            connection_type: "tcp" or "rtu"
            host: IP address for TCP connection
            port: Port number for TCP connection (default 502)
            unit_id: Modbus unit/slave ID
            timeout: Connection timeout in seconds
            retries: Number of retry attempts
            retry_delay: Delay between retries in seconds
            serial_port: Serial port for RTU connection (e.g., /dev/ttyUSB0)
            baudrate: Baud rate for RTU connection
            bytesize: Data bits for RTU connection
            parity: Parity for RTU connection (N/E/O)
            stopbits: Stop bits for RTU connection
        """
        self.connection_type = connection_type
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay

        # RTU parameters
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits

        self.client: Optional[Union[AsyncModbusTcpClient, AsyncModbusSerialClient]] = None
        self._connected = False
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        """
        Establish connection to Modbus device.

        Returns:
            True if connection successful, False otherwise
        """
        async with self._lock:
            if self._connected and self.client:
                return True

            try:
                if self.connection_type == "tcp":
                    if not self.host:
                        raise ValueError("Host must be specified for TCP connection")

                    self.client = AsyncModbusTcpClient(
                        host=self.host,
                        port=self.port,
                        timeout=self.timeout,
                        framer=ModbusSocketFramer,
                    )
                    logger.info(f"Connecting to Modbus TCP at {self.host}:{self.port}")

                elif self.connection_type == "rtu":
                    if not self.serial_port:
                        raise ValueError("Serial port must be specified for RTU connection")

                    self.client = AsyncModbusSerialClient(
                        port=self.serial_port,
                        baudrate=self.baudrate,
                        bytesize=self.bytesize,
                        parity=self.parity,
                        stopbits=self.stopbits,
                        timeout=self.timeout,
                        framer=ModbusRtuFramer,
                    )
                    logger.info(f"Connecting to Modbus RTU at {self.serial_port}")

                else:
                    raise ValueError(f"Invalid connection type: {self.connection_type}")

                await self.client.connect()
                self._connected = self.client.connected

                if self._connected:
                    logger.info(f"Successfully connected to Modbus device (unit {self.unit_id})")
                else:
                    logger.error("Failed to connect to Modbus device")

                return self._connected

            except Exception as e:
                logger.error(f"Error connecting to Modbus device: {e}")
                self._connected = False
                return False

    async def disconnect(self):
        """Close connection to Modbus device."""
        async with self._lock:
            if self.client:
                self.client.close()
                self._connected = False
                logger.info("Disconnected from Modbus device")

    async def _ensure_connected(self) -> bool:
        """Ensure client is connected, attempt reconnection if needed."""
        if self._connected and self.client and self.client.connected:
            return True

        return await self.connect()

    async def read_holding_registers(
        self,
        address: int,
        count: int = 1,
    ) -> Optional[list]:
        """
        Read holding registers (function code 3).

        Args:
            address: Starting register address
            count: Number of registers to read

        Returns:
            List of register values or None on failure
        """
        for attempt in range(self.retries + 1):
            try:
                if not await self._ensure_connected():
                    logger.warning(f"Not connected, attempt {attempt + 1}/{self.retries + 1}")
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                    continue

                result = await self.client.read_holding_registers(
                    address=address,
                    count=count,
                    slave=self.unit_id,
                )

                if result.isError():
                    logger.warning(
                        f"Error reading holding registers at {address}: {result}"
                    )
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                    continue

                return result.registers

            except ModbusException as e:
                logger.warning(
                    f"Modbus exception reading registers at {address} "
                    f"(attempt {attempt + 1}/{self.retries + 1}): {e}"
                )
                self._connected = False
                await asyncio.sleep(self.retry_delay * (2 ** attempt))
            except Exception as e:
                logger.error(f"Unexpected error reading registers at {address}: {e}")
                self._connected = False
                await asyncio.sleep(self.retry_delay * (2 ** attempt))

        logger.error(
            f"Failed to read holding registers at {address} after {self.retries + 1} attempts"
        )
        return None

    async def read_input_registers(
        self,
        address: int,
        count: int = 1,
    ) -> Optional[list]:
        """
        Read input registers (function code 4).

        Args:
            address: Starting register address
            count: Number of registers to read

        Returns:
            List of register values or None on failure
        """
        for attempt in range(self.retries + 1):
            try:
                if not await self._ensure_connected():
                    logger.warning(f"Not connected, attempt {attempt + 1}/{self.retries + 1}")
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                    continue

                result = await self.client.read_input_registers(
                    address=address,
                    count=count,
                    slave=self.unit_id,
                )

                if result.isError():
                    logger.warning(
                        f"Error reading input registers at {address}: {result}"
                    )
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                    continue

                return result.registers

            except ModbusException as e:
                logger.warning(
                    f"Modbus exception reading input registers at {address} "
                    f"(attempt {attempt + 1}/{self.retries + 1}): {e}"
                )
                self._connected = False
                await asyncio.sleep(self.retry_delay * (2 ** attempt))
            except Exception as e:
                logger.error(f"Unexpected error reading input registers at {address}: {e}")
                self._connected = False
                await asyncio.sleep(self.retry_delay * (2 ** attempt))

        logger.error(
            f"Failed to read input registers at {address} after {self.retries + 1} attempts"
        )
        return None

    def _decode_int16(self, registers: list) -> int:
        """Decode signed 16-bit integer from 1 register."""
        value = registers[0]
        return value if value < 32768 else value - 65536

    def _decode_uint16(self, registers: list) -> int:
        """Decode unsigned 16-bit integer from 1 register."""
        return registers[0]

    def _decode_int32(self, registers: list) -> int:
        """Decode signed 32-bit integer from 2 registers (big-endian)."""
        bytes_data = struct.pack(">HH", registers[0], registers[1])
        return struct.unpack(">i", bytes_data)[0]

    def _decode_uint32(self, registers: list) -> int:
        """Decode unsigned 32-bit integer from 2 registers (big-endian)."""
        bytes_data = struct.pack(">HH", registers[0], registers[1])
        return struct.unpack(">I", bytes_data)[0]

    def _decode_float32(self, registers: list) -> float:
        """Decode IEEE 754 float from 2 registers (big-endian)."""
        bytes_data = struct.pack(">HH", registers[0], registers[1])
        return struct.unpack(">f", bytes_data)[0]

    def _decode_float64(self, registers: list) -> float:
        """Decode IEEE 754 double from 4 registers (big-endian)."""
        bytes_data = struct.pack(">HHHH", registers[0], registers[1], registers[2], registers[3])
        return struct.unpack(">d", bytes_data)[0]

    async def read_register(
        self,
        address: int,
        register_type: Literal["holding", "input"] = "holding",
        data_type: Literal["int16", "uint16", "int32", "uint32", "float32", "float64"] = "int16",
        scale: float = 1.0,
    ) -> Optional[Union[int, float]]:
        """
        Read and decode a register value.

        Args:
            address: Register address
            register_type: "holding" or "input"
            data_type: Data type to decode
            scale: Scale factor to apply to raw value

        Returns:
            Decoded and scaled value or None on failure
        """
        # Determine number of registers to read
        count_map = {
            "int16": 1,
            "uint16": 1,
            "int32": 2,
            "uint32": 2,
            "float32": 2,
            "float64": 4,
        }
        count = count_map[data_type]

        # Read registers
        if register_type == "holding":
            registers = await self.read_holding_registers(address, count)
        else:
            registers = await self.read_input_registers(address, count)

        if registers is None:
            return None

        # Decode based on data type
        decode_map = {
            "int16": self._decode_int16,
            "uint16": self._decode_uint16,
            "int32": self._decode_int32,
            "uint32": self._decode_uint32,
            "float32": self._decode_float32,
            "float64": self._decode_float64,
        }

        try:
            value = decode_map[data_type](registers)
            return value * scale
        except Exception as e:
            logger.error(f"Error decoding register at {address}: {e}")
            return None

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected and self.client is not None and self.client.connected
