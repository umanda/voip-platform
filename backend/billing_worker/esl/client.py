import asyncio

import structlog

logger = structlog.get_logger(__name__)


class ESLClient:
    """
    FreeSWITCH Event Socket Library (ESL) async client.

    Connects to FreeSWITCH ESL port (default 8021) over TCP and subscribes
    to call lifecycle events. The billing worker uses this to receive
    CHANNEL_HANGUP_COMPLETE events for CDR finalization.

    Protocol:
        ESL is a line-oriented text protocol over TCP.
        - Server sends "Content-Type: auth/request\\n\\n" on connect.
        - Client replies "auth <password>\\n\\n".
        - Server replies "Reply-Text: +OK accepted\\n\\n".
        - Client subscribes: "events plain <EVENT1> <EVENT2>\\n\\n".
        - Events arrive as multi-line blocks terminated by double newline.
        - Events with a body include Content-Length header; body follows.

    Security note (R-INFRA-04):
        ESL must be bound to the internal interface only and reachable solely
        from the billing worker ECS task security group. Never expose port 8021
        to the internet or to the public subnet.
    """

    def __init__(self, host: str, port: int, password: str) -> None:
        self.host = host
        self.port = port
        self.password = password
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False

    async def connect(self) -> None:
        """Open TCP connection to FreeSWITCH ESL and authenticate."""
        logger.info("esl_connecting", host=self.host, port=self.port)
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

        # Wait for auth/request prompt from FreeSWITCH
        await self._read_block()

        self._writer.write(f"auth {self.password}\n\n".encode())
        await self._writer.drain()

        response = await self._read_block()
        if "+OK accepted" not in response:
            raise ConnectionError(f"ESL authentication failed: {response!r}")

        self._connected = True
        logger.info("esl_authenticated", host=self.host, port=self.port)

    async def subscribe(self, events: list[str]) -> None:
        """
        Subscribe to a list of FreeSWITCH event types.

        Args:
            events: e.g. ["CHANNEL_HANGUP_COMPLETE", "CHANNEL_ANSWER"]
        """
        cmd = f"events plain {' '.join(events)}\n\n"
        self._writer.write(cmd.encode())  # type: ignore[union-attr]
        await self._writer.drain()  # type: ignore[union-attr]
        await self._read_block()   # consume +OK response
        logger.info("esl_subscribed", events=events)

    async def read_event(self) -> dict[str, str] | None:
        """
        Read one ESL event from the stream.

        Returns:
            Parsed header dict (plus body lines merged in), or None on EOF/disconnect.
        """
        try:
            header_block = await self._read_block()
            if not header_block:
                return None

            headers: dict[str, str] = {}
            for line in header_block.splitlines():
                if ": " in line:
                    key, _, value = line.partition(": ")
                    headers[key.strip()] = value.strip()

            # Read body if present (Content-Length header)
            if "Content-Length" in headers:
                body_bytes = await self._reader.readexactly(  # type: ignore[union-attr]
                    int(headers["Content-Length"])
                )
                for line in body_bytes.decode().splitlines():
                    if ": " in line:
                        key, _, value = line.partition(": ")
                        headers[key.strip()] = value.strip()

            return headers

        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            logger.warning("esl_stream_ended")
            self._connected = False
            return None

    async def execute_api(self, command: str) -> str:
        """
        Run a FreeSWITCH API command synchronously and return the result.

        Used for crash-recovery: `show calls` returns live call UUIDs
        so the billing worker can detect orphaned Redis sessions (R-BILL-05).
        """
        self._writer.write(f"api {command}\n\n".encode())  # type: ignore[union-attr]
        await self._writer.drain()  # type: ignore[union-attr]
        return await self._read_block()

    async def _read_block(self) -> str:
        """Read lines from the ESL stream until a blank line (block boundary)."""
        lines: list[str] = []
        while True:
            raw = await self._reader.readline()  # type: ignore[union-attr]
            line = raw.decode().rstrip("\n\r")
            if line == "":
                break
            lines.append(line)
        return "\n".join(lines)

    async def disconnect(self) -> None:
        """Close the ESL connection."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._connected = False
        logger.info("esl_disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected
