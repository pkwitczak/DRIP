"""Application communication using aiohttp."""

import asyncio
import json
import ssl

import aiohttp


class CommunicationError(Exception):
    pass


class InvalidMessage(CommunicationError):
    pass


class HttpStatusError(CommunicationError):
    def __init__(self, status):
        self.status = status
        super().__init__(
            "Unexpected HTTP status: %s" % status
        )


class _HttpRequest:
    """Keep an HTTP session alive for the response lifetime."""

    def __init__(
        self,
        client,
        method,
        url,
        data=None,
        headers=None,
    ):
        self._client = client
        self._method = method
        self._url = url
        self._data = data
        self._headers = headers

        self._session = None
        self._context = None

    async def __aenter__(self):
        client = self._client
        client._require_open()

        headers = dict(client.headers)
        headers.update(self._headers or {})

        self._session = client._new_session()

        try:
            kwargs = {
                "headers": headers,
            }
            kwargs.update(
                client._ssl_kwargs(self._url)
            )

            if self._method == "GET":
                self._context = self._session.get(
                    self._url,
                    **kwargs
                )
            else:
                self._context = self._session.post(
                    self._url,
                    data=self._data,
                    **kwargs
                )

            response = await self._context.__aenter__()

            client._require_open()

            return response

        except BaseException:
            await client._close_session(self._session)
            raise

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        try:
            return await self._context.__aexit__(
                exc_type,
                exc_value,
                traceback,
            )
        finally:
            await self._client._close_session(
                self._session
            )


class Client:
    """
    Configuration:
        CLIENT_VERIFY_SSL: bool, default False
        CLIENT_HEADERS: dict[str, str], default {}

    Addresses are supplied to individual operations.

    send(url, data):
        HTTP POST or WebSocket message.

    receive(url):
        HTTP GET or next WebSocket message.

    open(url):
        Streaming HTTP GET context for OTA.
    """

    def __init__(self, config):
        self.verify_ssl = config.get(
            "CLIENT_VERIFY_SSL",
            False,
        )
        self.headers = config.get(
            "CLIENT_HEADERS",
            {},
        )

        if type(self.verify_ssl) is not bool:
            raise TypeError(
                "CLIENT_VERIFY_SSL must be a bool"
            )

        if not isinstance(self.headers, dict):
            raise TypeError(
                "CLIENT_HEADERS must be a dictionary"
            )

        for name, value in self.headers.items():
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
            ):
                raise TypeError(
                    "CLIENT_HEADERS must contain "
                    "string names and values"
                )

        self._ssl_ctx = None
        self._sessions = []
        self._websockets = {}
        self._connect_lock = asyncio.Lock()
        self._closed = False

    def _require_open(self):
        if self._closed:
            raise CommunicationError(
                "Client is closed"
            )

    def _validate_url(self, url, http_only=False):
        if not isinstance(url, str):
            raise TypeError(
                "URL must be a string"
            )

        schemes = (
            ("http://", "https://")
            if http_only
            else (
                "http://",
                "https://",
                "ws://",
                "wss://",
            )
        )

        if not url.startswith(schemes):
            raise ValueError(
                "Unsupported URL scheme"
            )

    def _create_ssl_context(self):
        context = ssl.SSLContext(
            ssl.PROTOCOL_TLS_CLIENT
        )

        if not self.verify_ssl:
            if hasattr(context, "check_hostname"):
                context.check_hostname = False

            context.verify_mode = ssl.CERT_NONE
        else:
            context.verify_mode = ssl.CERT_REQUIRED

        return context

    def _ssl_kwargs(self, url):
        if not url.startswith(
            ("https://", "wss://")
        ):
            return {}

        if self._ssl_ctx is None:
            self._ssl_ctx = self._create_ssl_context()

        return {
            "ssl": self._ssl_ctx,
        }

    def _new_session(self):
        self._require_open()

        session = aiohttp.ClientSession(
            headers=dict(self.headers)
        )
        self._sessions.append(session)

        return session

    async def _close_session(self, session):
        if session not in self._sessions:
            return

        try:
            await session.close()
        finally:
            if session in self._sessions:
                self._sessions.remove(session)

    def open(self, url, headers=None):
        """Return a streaming HTTP GET context."""

        self._require_open()
        self._validate_url(url, http_only=True)

        return _HttpRequest(
            self,
            "GET",
            url,
            headers=headers,
        )

    def post(self, url, data, headers=None):
        """Return an HTTP POST context."""

        self._require_open()
        self._validate_url(url, http_only=True)

        request_headers = dict(headers or {})

        if isinstance(data, dict):
            data = json.dumps(data)

            has_content_type = any(
                name.lower() == "content-type"
                for source in (
                    self.headers,
                    request_headers,
                )
                for name in source
            )

            if not has_content_type:
                request_headers["Content-Type"] = (
                    "application/json"
                )

        return _HttpRequest(
            self,
            "POST",
            url,
            data=data,
            headers=request_headers,
        )

    def checked(self, response, expected=200):
        """Check HTTP status without consuming the body."""

        if expected is None:
            accepted = 200 <= response.status < 300
        else:
            accepted = response.status == expected

        if not accepted:
            raise HttpStatusError(response.status)

        return response

    def _decode_message(self, payload):
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")

            message = json.loads(payload)

        except ValueError as exc:
            raise InvalidMessage(
                "Invalid JSON message: %s" % exc
            )

        if not isinstance(message, dict):
            raise InvalidMessage(
                "Received JSON must be an object"
            )

        return message

    async def _get_websocket(self, url):
        self._require_open()

        async with self._connect_lock:
            self._require_open()

            entry = self._websockets.get(url)

            if entry is not None:
                return entry

            session = self._new_session()

            try:
                context = session.ws_connect(
                    url,
                    **self._ssl_kwargs(url)
                )
                socket = await context.__aenter__()

                self._require_open()

            except BaseException:
                await self._close_session(session)
                raise

            entry = {
                "session": session,
                "context": context,
                "socket": socket,
                "reader": socket.__aiter__(),
                "read_lock": asyncio.Lock(),
                "write_lock": asyncio.Lock(),
            }
            self._websockets[url] = entry

            return entry

    async def _drop_websocket(self, url, entry):
        if self._websockets.get(url) is not entry:
            return

        del self._websockets[url]

        try:
            await entry["context"].__aexit__(
                None,
                None,
                None,
            )
        finally:
            await self._close_session(
                entry["session"]
            )

    async def send(self, url, data):
        """Send a dictionary using HTTP POST or WebSocket."""

        self._require_open()
        self._validate_url(url)

        if not isinstance(data, dict):
            raise TypeError(
                "Outgoing message must be a dictionary"
            )

        if url.startswith(("ws://", "wss://")):
            entry = await self._get_websocket(url)

            try:
                async with entry["write_lock"]:
                    if self._websockets.get(url) is not entry:
                        raise CommunicationError(
                            "WebSocket connection changed"
                        )

                    await entry["socket"].send_str(
                        json.dumps(data)
                    )

            except (OSError, CommunicationError):
                await self._drop_websocket(url, entry)
                raise

            except asyncio.CancelledError:
                await self._drop_websocket(url, entry)
                raise

            return

        async with self.post(url, data) as response:
            self.checked(response, expected=None)

    async def receive(self, url):
        """Return a dictionary or None when no message is available."""

        self._require_open()
        self._validate_url(url)

        if not url.startswith(("ws://", "wss://")):
            async with self.open(url) as response:
                self.checked(response, expected=None)

                if response.status in (204, 205):
                    return None

                text = await response.text()

                if not text:
                    return None

                return self._decode_message(text)

        entry = await self._get_websocket(url)

        try:
            async with entry["read_lock"]:
                if self._websockets.get(url) is not entry:
                    raise CommunicationError(
                        "WebSocket connection changed"
                    )

                while True:
                    message = await entry["reader"].__anext__()

                    if message.type in (
                        aiohttp.WSMsgType.TEXT,
                        aiohttp.WSMsgType.BINARY,
                    ):
                        return self._decode_message(
                            message.data
                        )

                    if message.type == aiohttp.WSMsgType.ERROR:
                        raise CommunicationError(
                            "WebSocket connection error"
                        )

        except StopAsyncIteration:
            await self._drop_websocket(url, entry)
            return None

        except InvalidMessage:
            raise

        except (OSError, CommunicationError):
            await self._drop_websocket(url, entry)
            raise

        except asyncio.CancelledError:
            await self._drop_websocket(url, entry)
            raise

    async def close(self):
        self._closed = True
        failure = None

        async with self._connect_lock:
            for url, entry in list(
                self._websockets.items()
            ):
                try:
                    await self._drop_websocket(
                        url,
                        entry,
                    )
                except Exception as exc:
                    if failure is None:
                        failure = exc

            for session in list(self._sessions):
                try:
                    await self._close_session(session)
                except Exception as exc:
                    if failure is None:
                        failure = exc

        if failure is not None:
            raise failure

    async def __aenter__(self):
        self._require_open()
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        await self.close()
        return False
