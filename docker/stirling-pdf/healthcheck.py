#!/usr/bin/env python3
"""Check the HTTP service and both local office workers without submitting a job."""
import socket
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8080/api/v1/info/status", timeout=3) as response:
    assert response.status == 200
for port in (2003, 2005):
    with socket.create_connection(("127.0.0.1", port), timeout=2):
        pass
