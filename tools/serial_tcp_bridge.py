"""
Expose a serial port over TCP, with explicit control of the DTR/RTS lines.

Why this exists: on some EFR32 Zigbee dongles (the ZB-GW04 among them) DTR is wired to
the radio's RESET pin. node-serialport — and therefore Zigbee2MQTT's ember adapter —
asserts DTR when it opens the port, which holds the radio in reset forever: the port
opens cleanly, and the NCP answers nothing at any baud rate. There is no Zigbee2MQTT
setting for the line states.

The fix is to open the port here, with the states the hardware actually needs, and let
Zigbee2MQTT talk to a socket instead (its ember adapter accepts `tcp://host:port`):

    python tools/serial_tcp_bridge.py --serial /dev/cu.usbserial-2110 --no-dtr --rts

    # zigbee2mqtt configuration.yaml
    serial:
      port: tcp://127.0.0.1:20108
      adapter: ember

Verify the radio is alive through the bridge before starting Zigbee2MQTT:

    python tools/serial_tcp_bridge.py --serial <port> --no-dtr --rts --probe

which sends an ASH reset and expects an RSTACK (a frame beginning 0xC1) back.
"""
from __future__ import annotations

import argparse
import selectors
import socket
import sys
import time

import serial

ASH_RESET = bytes([0x1A, 0xC0, 0x38, 0xBC, 0x7E])


def open_serial(args: argparse.Namespace) -> serial.Serial:
    ser = serial.Serial()
    ser.port = args.serial
    ser.baudrate = args.baud
    ser.timeout = 0            # non-blocking; the selector decides when to read
    ser.rtscts = args.rtscts
    # set BEFORE open so the radio is never briefly held in reset
    ser.dtr = args.dtr
    ser.rts = args.rts
    ser.open()
    ser.dtr = args.dtr         # and again after: some drivers reset them on open
    ser.rts = args.rts
    time.sleep(args.settle)    # let the radio boot if it was in reset
    ser.reset_input_buffer()
    return ser


def probe(ser: serial.Serial) -> int:
    """Send an ASH reset; a live EmberZNet NCP replies RSTACK."""
    ser.reset_input_buffer()
    ser.write(ASH_RESET)
    ser.flush()
    deadline = time.time() + 2.0
    data = b""
    while time.time() < deadline:
        data += ser.read(64)
        if b"\xc1" in data:
            print(f"RSTACK received: {data.hex()} — the radio is alive")
            return 0
        time.sleep(0.05)
    print(f"no RSTACK (got {data.hex() or 'nothing'}). Try flipping --dtr/--rts or the baud rate.",
          file=sys.stderr)
    return 1


def pump(ser: serial.Serial, conn: socket.socket) -> None:
    """Shuttle bytes both ways until the client goes away."""
    conn.setblocking(False)
    sel = selectors.DefaultSelector()
    sel.register(conn, selectors.EVENT_READ, "sock")
    sel.register(ser.fileno(), selectors.EVENT_READ, "serial")
    try:
        while True:
            for key, _ in sel.select(timeout=1.0):
                if key.data == "sock":
                    data = conn.recv(4096)
                    if not data:
                        return
                    ser.write(data)
                else:
                    data = ser.read(4096)
                    if data:
                        conn.sendall(data)
    except (ConnectionResetError, BrokenPipeError, OSError):
        return
    finally:
        sel.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Serial-over-TCP with explicit DTR/RTS")
    ap.add_argument("--serial", required=True, help="e.g. /dev/cu.usbserial-2110")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=20108)
    ap.add_argument("--rtscts", action="store_true", help="hardware flow control")
    ap.add_argument("--settle", type=float, default=2.0,
                    help="seconds to wait after opening, for the radio to boot")
    ap.add_argument("--probe", action="store_true",
                    help="check the radio answers, then exit (does not serve)")
    dtr = ap.add_mutually_exclusive_group()
    dtr.add_argument("--dtr", dest="dtr", action="store_true", help="assert DTR")
    dtr.add_argument("--no-dtr", dest="dtr", action="store_false",
                     help="de-assert DTR (needed when DTR is wired to the radio's reset)")
    rts = ap.add_mutually_exclusive_group()
    rts.add_argument("--rts", dest="rts", action="store_true", help="assert RTS")
    rts.add_argument("--no-rts", dest="rts", action="store_false", help="de-assert RTS")
    ap.set_defaults(dtr=False, rts=True)      # what the ZB-GW04 needs
    args = ap.parse_args()

    ser = open_serial(args)
    if args.probe:
        sys.exit(probe(ser))

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(1)
    print(f"serial {args.serial} @{args.baud} (dtr={args.dtr}, rts={args.rts}) "
          f"-> tcp://{args.host}:{args.port}", flush=True)
    try:
        while True:
            conn, addr = srv.accept()
            print(f"client connected: {addr[0]}:{addr[1]}", flush=True)
            # a fresh consumer expects a clean stream, not the previous session's tail
            ser.reset_input_buffer()
            pump(ser, conn)
            conn.close()
            print("client disconnected", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
        ser.close()


if __name__ == "__main__":
    main()
