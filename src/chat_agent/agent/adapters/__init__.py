from .cli import CLIAdapter
from .formatting import markdown_to_plaintext
from .gmail import GmailAdapter
from .protocol import ChannelAdapter
from .scheduler import SchedulerAdapter

__all__ = [
    "ChannelAdapter",
    "CLIAdapter",
    "GmailAdapter",
    "SchedulerAdapter",
    "markdown_to_plaintext",
]
