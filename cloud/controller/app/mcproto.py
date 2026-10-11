"""Just enough of the Minecraft Java protocol to answer server-list pings, kick a joining player with a
message, and ping the real server. Everything else is relayed as raw bytes by the gateway."""
import asyncio
import json
import struct


class ProtocolError(Exception):
    pass


def encode_varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _signed(value: int) -> int:
    return value - (1 << 32) if value & 0x80000000 else value


def decode_varint(buf: bytes, offset: int = 0):
    result = 0
    for i in range(5):
        if offset + i >= len(buf):
            raise ProtocolError("truncated varint")
        byte = buf[offset + i]
        result |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return _signed(result), offset + i + 1
    raise ProtocolError("varint too long")


async def read_varint(reader, raw: bytearray):
    result = 0
    for i in range(5):
        byte = (await reader.readexactly(1))[0]
        raw.append(byte)
        result |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return _signed(result)
    raise ProtocolError("varint too long")


def encode_string(text: str) -> bytes:
    data = text.encode("utf-8")
    return encode_varint(len(data)) + data


def decode_string(buf: bytes, offset: int = 0, limit: int = 1024):
    length, offset = decode_varint(buf, offset)
    if length < 0 or length > limit or offset + length > len(buf):
        raise ProtocolError("bad string length")
    return buf[offset:offset + length].decode("utf-8", "replace"), offset + length


def frame(packet_id: int, payload: bytes = b"") -> bytes:
    body = encode_varint(packet_id) + payload
    return encode_varint(len(body)) + body


async def read_packet(reader, raw: bytearray, max_len: int = 4096):
    """Read one packet. Every byte consumed is appended to `raw` so it can be replayed to the real server."""
    length = await read_varint(reader, raw)
    if length <= 0 or length > max_len:
        raise ProtocolError(f"bad packet length {length}")
    body = await reader.readexactly(length)
    raw += body
    packet_id, offset = decode_varint(body)
    return packet_id, body[offset:]


def parse_handshake(data: bytes) -> dict:
    protocol, off = decode_varint(data)
    host, off = decode_string(data, off, 512)
    if off + 2 > len(data):
        raise ProtocolError("truncated handshake")
    port = struct.unpack(">H", data[off:off + 2])[0]
    next_state, _ = decode_varint(data, off + 2)
    return {"protocol": protocol, "host": host, "port": port, "next_state": next_state}


def parse_login_name(data: bytes) -> str:
    name, _ = decode_string(data, 0, 256)
    return name


def status_response(protocol: int, version_name: str, motd: str, online: int = 0, maximum: int = 0) -> bytes:
    body = {
        "version": {"name": version_name, "protocol": protocol},
        "players": {"max": maximum, "online": online},
        "description": {"text": motd},
    }
    return frame(0x00, encode_string(json.dumps(body, ensure_ascii=False, separators=(",", ":"))))


def login_disconnect(message: str, color: str = "yellow") -> bytes:
    return frame(0x00, encode_string(json.dumps({"text": message, "color": color}, ensure_ascii=False)))


async def ping_server(host: str, port: int, timeout: float = 3.0) -> dict:
    """Server-list ping against the real server. Raises if it isn't accepting connections yet."""
    async def go():
        reader, writer = await asyncio.open_connection(host, port)
        try:
            handshake = encode_varint(-1) + encode_string(host) + struct.pack(">H", port) + encode_varint(1)
            writer.write(frame(0x00, handshake) + frame(0x00))
            await writer.drain()
            packet_id, data = await read_packet(reader, bytearray(), max_len=1 << 22)
            if packet_id != 0x00:
                raise ProtocolError("unexpected status packet")
            text, _ = decode_string(data, 0, 1 << 22)
            return json.loads(text)
        finally:
            writer.close()

    return await asyncio.wait_for(go(), timeout)
