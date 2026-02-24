from .cli import CLIAdapter
from .discord import DiscordAdapter
from .formatting import markdown_to_plaintext
from .gmail import GmailAdapter
from .line_crack import LineCrackAdapter
from .protocol import ChannelAdapter
from .scheduler import SchedulerAdapter

__all__ = [
    "ChannelAdapter",
    "CLIAdapter",
    "DiscordAdapter",
    "GmailAdapter",
    "LineCrackAdapter",
    "SchedulerAdapter",
    "markdown_to_plaintext",
]
