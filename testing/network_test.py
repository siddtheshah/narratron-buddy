"""Regression coverage for offline tests and local event-loop communication."""

import asyncio
from pathlib import Path
import socket

from dotenv import load_dotenv
import pytest
from pytest_socket import SocketBlockedError


def test_internet_sockets_and_dns_are_blocked() -> None:
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    with pytest.raises(SocketBlockedError):
        socket.getaddrinfo("example.test", 443)


def test_socketpair_can_exchange_data_without_lifting_network_guard() -> None:
    reader, writer = socket.socketpair()
    with reader, writer:
        writer.sendall(b"wake")
        assert reader.recv(4) == b"wake"
        with pytest.raises(SocketBlockedError):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_asyncio_can_use_its_wakeup_pipe() -> None:
    async def run_in_thread() -> str:
        return await asyncio.to_thread(str, "local")

    assert asyncio.run(run_in_thread()) == "local"


def test_dotenv_cannot_load_developer_credentials(tmp_path: Path) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("NARRATRON_HERMETIC_PROBE=loaded\n", encoding="utf-8")
    assert load_dotenv(dotenv_path) is False
