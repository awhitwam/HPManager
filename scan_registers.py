from pymodbus.client import ModbusTcpClient
import time
import sys
import logging

# --- CONFIGURATION ---
IP_ADDRESS = '192.168.8.74'
PORT = 502
SLAVE_ID = 1

# Stiebel Eltron WPM range: blocks at 500, 1500, 2500, 3500
START_ADDR = 1
END_ADDR = 4000

SCAN_DELAY = 0.02
# ---------------------

logging.getLogger('pymodbus').setLevel(logging.CRITICAL)

def silent_read(client, addr, func_type):
    try:
        if func_type == 'INPUT':
            rr = client.read_input_registers(addr, count=1, slave=SLAVE_ID)
        else:
            rr = client.read_holding_registers(addr, count=1, slave=SLAVE_ID)

        if rr.isError(): return None
        return rr.registers[0]
    except:
        return None

# --- MAIN ---
print(f"--- REGISTER DUMP ({START_ADDR}-{END_ADDR}) ---")
print(f"Target: {IP_ADDRESS}:{PORT} slave={SLAVE_ID}")
print(f"Checking both INPUT and HOLDING for every address\n")

client = ModbusTcpClient(IP_ADDRESS, port=PORT)
if not client.connect():
    print("Connection failed.")
    sys.exit()

found_count = 0

try:
    for addr in range(START_ADDR, END_ADDR + 1):

        # INPUT REGISTERS (FC 04)
        val_i = silent_read(client, addr, 'INPUT')
        if val_i is not None and val_i != 32768:
            signed = val_i - 65536 if val_i > 32767 else val_i
            print(f"  [INPUT]   Addr {addr:>5}: raw={val_i:>6}  signed={signed:>6}")
            found_count += 1

        # HOLDING REGISTERS (FC 03)
        val_h = silent_read(client, addr, 'HOLDING')
        if val_h is not None and val_h != 32768:
            signed = val_h - 65536 if val_h > 32767 else val_h
            print(f"  [HOLDING] Addr {addr:>5}: raw={val_h:>6}  signed={signed:>6}")
            found_count += 1

        if addr % 100 == 0:
            sys.stdout.write(f"\rScanning {addr}/{END_ADDR} found {found_count}...")
            sys.stdout.flush()

        time.sleep(SCAN_DELAY)

except KeyboardInterrupt:
    print("\n\n  Stopped by user.")

client.close()
print(f"\n\n--- Scan Complete. Found {found_count} readable registers. ---")
