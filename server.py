#!/usr/bin/env python3
"""
Content server using raw TCP sockets (no http.server).

Application protocol (text header line, then binary body):
  client -> server : "GET <size_bytes>\n"
  server -> client : "OK <server_name> <size_bytes>\n" + <size_bytes of data>
  client -> server : "PING\n"
  server -> client : "PONG <server_name> <active_connections>\n"
  on bad input     : "ERR <reason>\n"

Concurrency : one thread per accepted connection.
Health      : heartbeat JSON written every second to /tmp/cds_status/<name>.json
              (the SDN controller reads it to decide if the server is alive
              and how loaded it is).
Usage       : python3 server.py <name> [port]
"""
import json
import os
import socket
import sys
import threading
import time

NAME = sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
STATUS_DIR = '/tmp/cds_status'
MAX_SIZE = 50_000_000          # reject absurd requests
CONN_TIMEOUT = 10              # seconds of inactivity before dropping a client
CHUNK = b'x' * 16384

os.makedirs(STATUS_DIR, exist_ok=True)
lock = threading.Lock()
active = 0
total = 0


def read_line(conn, limit=128):
    """Read bytes until newline (or limit). Returns str or None on EOF."""
    buf = b''
    while b'\n' not in buf and len(buf) < limit:
        data = conn.recv(64)
        if not data:
            return None
        buf += data
    return buf.split(b'\n', 1)[0].decode('ascii', 'replace').strip()


def handle(conn, addr):
    global active, total
    with lock:
        active += 1
        total += 1
    try:
        conn.settimeout(CONN_TIMEOUT)
        line = read_line(conn)
        if line is None:
            return
        parts = line.split()
        if len(parts) == 2 and parts[0] == 'GET' and parts[1].isdigit():
            size = int(parts[1])
            if size > MAX_SIZE:
                conn.sendall(b'ERR size-too-large\n')
                return
            conn.sendall(f'OK {NAME} {size}\n'.encode())
            sent = 0
            while sent < size:
                n = min(len(CHUNK), size - sent)
                conn.sendall(CHUNK[:n])
                sent += n
        elif parts == ['PING']:
            with lock:
                a = active
            conn.sendall(f'PONG {NAME} {a}\n'.encode())
        else:
            conn.sendall(b'ERR bad-request\n')
    except (socket.timeout, BrokenPipeError, ConnectionResetError, OSError):
        pass                      # client vanished or path failed mid-transfer
    finally:
        with lock:
            active -= 1
        try:
            conn.close()
        except OSError:
            pass


def heartbeat():
    while True:
        try:
            os.makedirs(STATUS_DIR, exist_ok=True)
            with lock:
                d = {'ts': time.time(), 'active': active, 'total': total}
            tmp = f'{STATUS_DIR}/{NAME}.tmp'
            with open(tmp, 'w') as f:
                json.dump(d, f)
            os.replace(tmp, f'{STATUS_DIR}/{NAME}.json')
        except OSError:
            pass
        time.sleep(1)


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', PORT))
    srv.listen(128)
    threading.Thread(target=heartbeat, daemon=True).start()
    print(f'[{NAME}] listening on 0.0.0.0:{PORT}', flush=True)
    while True:
        try:
            conn, addr = srv.accept()
        except OSError:
            break
        threading.Thread(target=handle, args=(conn, addr), daemon=True).start()


if __name__ == '__main__':
    main()
