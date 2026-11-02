#!/usr/bin/env python3
"""
Download client using raw TCP sockets (no http.client).

Each request opens a NEW TCP connection to the virtual IP, so the SDN
controller sees a fresh SYN and can pick the best server / path for it.
If an attempt fails (server crash, link failure, timeout) the client
retries, and the retry is again load-balanced by the controller.

Usage : python3 client.py [num_requests] [size_bytes] [gap_seconds]
Env   : CDS_HOST (default 10.0.0.100), CDS_PORT (default 8080)
Output: one line per attempt, e.g.
        12:01:07 req=3 server=srv2 status=OK time=0.84s
"""
import os
import socket
import sys
import time

HOST = os.environ.get('CDS_HOST', '10.0.0.100')
PORT = int(os.environ.get('CDS_PORT', '8080'))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
SIZE = int(sys.argv[2]) if len(sys.argv) > 2 else 500000
GAP = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
TIMEOUT = 4          # seconds, applies to connect and to each recv
MAX_ATTEMPTS = 3


def fetch(size):
    """One download over one TCP connection. Returns server name."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect((HOST, PORT))
        s.sendall(f'GET {size}\n'.encode())

        buf = b''                                   # read header line
        while b'\n' not in buf:
            data = s.recv(1024)
            if not data:
                raise ConnectionError('closed before header')
            buf += data
        header, body = buf.split(b'\n', 1)
        parts = header.decode('ascii', 'replace').split()
        if len(parts) != 3 or parts[0] != 'OK':
            raise ValueError('bad header: ' + header.decode('ascii', 'replace'))
        name, expected = parts[1], int(parts[2])

        got = len(body)                             # read the body
        while got < expected:
            data = s.recv(65536)
            if not data:
                raise ConnectionError('closed mid-transfer')
            got += len(data)
        return name
    finally:
        s.close()


def main():
    for i in range(N):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            t0 = time.time()
            try:
                srv = fetch(SIZE)
                status = 'OK'
            except Exception as e:
                srv, status = '-', 'FAIL:' + type(e).__name__
            print(f'{time.strftime("%H:%M:%S")} req={i} server={srv} '
                  f'status={status} time={time.time() - t0:.2f}s', flush=True)
            if status == 'OK':
                break
            time.sleep(0.5)                         # brief pause, then retry
        time.sleep(GAP)


if __name__ == '__main__':
    main()
