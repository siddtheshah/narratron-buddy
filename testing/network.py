"""Local Windows event-loop plumbing compatible with the network guard."""

from __future__ import annotations

from contextlib import ExitStack
import socket
import sys


_socket_type = socket.socket


def _local_socketpair(
    family: int = socket.AF_INET,
    type: int = socket.SOCK_STREAM,
    proto: int = 0,
) -> tuple[socket.socket, socket.socket]:
    """Create only an authenticated, already connected loopback pair.

    Windows implements socketpair using TCP, even for asyncio's wakeup pipe.
    Use the original constructor only here; never temporarily lift the guard,
    which would let unrelated threads make network calls.
    """
    if family not in (socket.AF_INET, socket.AF_INET6):
        raise ValueError("Only IPv4 and IPv6 socket pairs are supported")
    if type != socket.SOCK_STREAM or proto != 0:
        raise ValueError("Only stream socket pairs with protocol zero are supported")
    host = "127.0.0.1" if family == socket.AF_INET else "::1"
    with ExitStack() as cleanup:
        with _socket_type(family, type, proto) as listener:
            listener.bind((host, 0))
            listener.listen(1)
            client = _socket_type(family, type, proto)
            cleanup.callback(client.close)
            client.setblocking(False)
            try:
                client.connect(listener.getsockname())
            except (BlockingIOError, InterruptedError):
                pass
            client.setblocking(True)
            # socket.accept() wraps the descriptor with the guarded constructor.
            descriptor, _ = listener._accept()
            server = _socket_type(family, type, proto, fileno=descriptor)
            cleanup.callback(server.close)
        if (
            server.getsockname() != client.getpeername()
            or client.getsockname() != server.getpeername()
        ):
            raise ConnectionError("Unexpected socket pair peer")
        cleanup.pop_all()
        return server, client


def install_local_socketpair() -> None:
    """Keep Windows asyncio functional without allowing arbitrary TCP sockets."""
    if sys.platform == "win32":
        socket.socketpair = _local_socketpair
