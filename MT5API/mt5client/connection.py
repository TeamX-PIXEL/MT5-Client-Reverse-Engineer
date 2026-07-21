"""WebSocket connection layer with send/recv lock."""
import asyncio
import ssl
import websockets

from .crypto import aes_encrypt, aes_decrypt, STATIC_KEY
from .protocol import pack_wire, build_command, parse_response


class Connection:
    def __init__(self, host: str, port: int = 443):
        self.host = host
        self.port = port
        self.ws = None
        self._send_lock = asyncio.Lock()
        self.session_key: bytes | None = None

    async def connect(self):
        url = f"wss://{self.host}:{self.port}/terminal"
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        self.ws = await websockets.connect(
            url,
            ssl=ssl_ctx,
            ping_interval=None,
            additional_headers={'Origin': f'https://{self.host}:{self.port}'},
        )

    async def send_raw(self, data: bytes):
        await self.ws.send(data)

    async def send_encrypted(self, cmd_id: int, payload: bytes):
        """Encrypt payload with session key and send."""
        async with self._send_lock:
            enc = aes_encrypt(self.session_key, payload)
            await self.ws.send(pack_wire(cmd_id, enc))

    async def send_auth(self, payload: bytes):
        """Send auth command encrypted with static key."""
        async with self._send_lock:
            enc = aes_encrypt(STATIC_KEY, payload)
            await self.ws.send(pack_wire(0, enc))

    async def recv(self, timeout: float = 10) -> dict | None:
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        if isinstance(raw, bytes) and len(raw) > 8:
            return parse_response(aes_decrypt(self.session_key, raw[8:]))
        return None

    async def recv_auth(self, timeout: float = 10) -> dict | None:
        """Receive auth response decrypted with static key."""
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        if isinstance(raw, bytes) and len(raw) > 8:
            return parse_response(aes_decrypt(STATIC_KEY, raw[8:]))
        return None

    async def close(self):
        if self.ws:
            await self.ws.close()
            self.ws = None
