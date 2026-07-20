"""Channel adapter protocol for message routing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ..schema import OutboundMessage

if TYPE_CHECKING:
    from ..core import AgentCore


class ChannelAdapter(Protocol):
    """Protocol that all channel adapters must satisfy.

    Adapters bridge external message sources (CLI, LINE, scheduler, etc.)
    with the AgentCore queue.

    Turn lifecycle (called on **all** registered adapters, not just the
    message source):

    1. ``on_turn_start(channel)`` -- before any console output.  Adapters
       that own the terminal (CLI) must suspend I/O when *channel* differs
       from their own to avoid concurrent ANSI writes.
    2. (message processing, console output, LLM calls, ...)
    3. ``on_turn_complete()`` -- after processing finishes.  CLI uses this
       to re-enable the input prompt.
    """

    channel_name: str
    priority: int

    def start(self, agent: AgentCore) -> None:
        """Start the adapter. Called once before the queue loop begins."""
        ...

    def send(self, message: OutboundMessage) -> None:
        """Deliver a response to this channel."""
        ...

    def on_turn_start(self, channel: str) -> None:
        """Called on ALL adapters before a turn begins.

        *channel* is the source channel of the inbound message.
        Terminal-owning adapters should suspend their UI when
        ``channel != self.channel_name``.
        """
        ...

    def on_turn_complete(self) -> None:
        """Called on ALL adapters after a turn finishes.

        CLI adapter uses this to signal the input thread that the prompt
        can be shown again.  Other adapters may no-op.
        """
        ...

    def stop(self) -> None:
        """Stop the adapter. Called when the agent shuts down."""
        ...
