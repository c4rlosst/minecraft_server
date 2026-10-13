"""Minimal Docker Engine API client over the unix socket (stdlib only). Methods are blocking; callers
run them in a thread."""
import http.client
import json
import socket
import struct


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def _demux(data: bytes) -> str:
    """Docker multiplexes stdout/stderr into 8-byte-header frames unless the container has a TTY."""
    out, i = [], 0
    while i + 8 <= len(data):
        if data[i] in (0, 1, 2) and data[i + 1:i + 4] == b"\x00\x00\x00":
            size = struct.unpack(">I", data[i + 4:i + 8])[0]
            out.append(data[i + 8:i + 8 + size])
            i += 8 + size
        else:
            return data.decode("utf-8", "replace")
    return b"".join(out).decode("utf-8", "replace")


class Docker:
    def __init__(self, socket_path: str = "/var/run/docker.sock"):
        self.socket_path = socket_path

    def _request(self, method: str, path: str, timeout: float = 30):
        conn = _UnixConnection(self.socket_path, timeout)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def state(self, name: str) -> str:
        """'running', 'exited', 'created', ... or 'missing'."""
        status, body = self._request("GET", f"/containers/{name}/json")
        if status == 404:
            return "missing"
        if status != 200:
            raise RuntimeError(f"docker inspect {name}: HTTP {status}")
        return json.loads(body)["State"]["Status"]

    def start(self, name: str) -> None:
        status, body = self._request("POST", f"/containers/{name}/start")
        if status not in (204, 304):
            raise RuntimeError(f"docker start {name}: HTTP {status} {body[:200]!r}")

    def stop(self, name: str, grace: int = 120) -> None:
        status, body = self._request("POST", f"/containers/{name}/stop?t={grace}", timeout=grace + 60)
        if status not in (204, 304):
            raise RuntimeError(f"docker stop {name}: HTTP {status} {body[:200]!r}")

    def logs(self, name: str, tail: int = 200) -> str:
        status, body = self._request("GET", f"/containers/{name}/logs?stdout=1&stderr=1&tail={int(tail)}")
        if status != 200:
            raise RuntimeError(f"docker logs {name}: HTTP {status}")
        return _demux(body)
