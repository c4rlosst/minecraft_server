import asyncio
import struct


async def rcon(host: str, port: int, password: str, command: str, timeout: float = 5.0) -> str:
    """Run one command over RCON and return the reply."""
    async def go():
        reader, writer = await asyncio.open_connection(host, port)

        async def send(request_id: int, kind: int, body: str):
            data = body.encode("utf-8")
            writer.write(struct.pack("<iii", 10 + len(data), request_id, kind) + data + b"\x00\x00")
            await writer.drain()

        async def recv():
            (length,) = struct.unpack("<i", await reader.readexactly(4))
            data = await reader.readexactly(length)
            request_id, kind = struct.unpack("<ii", data[:8])
            return request_id, kind, data[8:-2].decode("utf-8", "replace")

        try:
            await send(1, 3, password)
            request_id, _, _ = await recv()
            if request_id == -1:
                raise RuntimeError("RCON authentication failed (check RCON_PASSWORD)")
            await send(2, 2, command)
            _, _, body = await recv()
            return body
        finally:
            writer.close()

    return await asyncio.wait_for(go(), timeout)
