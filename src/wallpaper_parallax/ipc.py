"""The queries and events needed from i3's IPC protocol."""

import asyncio
from contextlib import asynccontextmanager
import json
import struct

HEADER = struct.Struct("=6sII")
MAGIC = b"i3-ipc"
GET_WORKSPACES = 1
SUBSCRIBE = 2
GET_OUTPUTS = 3
WORKSPACE_EVENT = 1 << 31
OUTPUT_EVENT = WORKSPACE_EVENT | 1


async def receive(reader):
    magic, size, kind = HEADER.unpack(await reader.readexactly(HEADER.size))
    if magic != MAGIC or size > 16 * 1024 * 1024:
        raise ValueError("invalid i3 IPC message")
    return kind, json.loads(await reader.readexactly(size))


async def request(reader, writer, kind, payload=b""):
    writer.write(HEADER.pack(MAGIC, len(payload), kind) + payload)
    await writer.drain()
    reply_kind, reply = await receive(reader)
    if reply_kind != kind:
        raise ValueError(f"unexpected i3 IPC reply type {reply_kind}")
    return reply


@asynccontextmanager
async def connect(path):
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()
