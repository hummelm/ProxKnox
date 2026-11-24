#!/usr/bin/env python3
# syslog-listener.py — UDP 10514 -> file (debian-installer log of the PXE
# client, sent via log_host=192.168.200.2 log_port=10514).
# Usage: syslog-listener.py /srv/logs/di-syslog.log
import socket
import sys
import time

if len(sys.argv) != 2:
    print("usage: syslog-listener.py <file>", file=sys.stderr)
    sys.exit(1)

path = sys.argv[1]
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 10514))
with open(path, "a", encoding="utf-8", errors="replace") as logf:
    while True:
        data, addr = s.recvfrom(32768)
        line = data.decode(errors="replace").rstrip()
        logf.write(f"{addr[0]} {time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        logf.flush()
