"""Base adapter interface for IM platform integrations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Awaitable


@dataclass
class IncomingMessage:
    """Normalized message from any platform."""
    platform: str
    user_id: str
    user_name: str
    chat_id: str
    text: str


@dataclass
class OutgoingMessage:
    """Response to send back to a platform."""
    chat_id: str
    text: str


class BaseAdapter(ABC):
    """Interface that all IM platform adapters must implement."""

    @abstractmethod
    async def start(self) -> None:
        """Start listening for messages."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop listening and clean up."""

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> None:
        """Send a message back to the platform."""
