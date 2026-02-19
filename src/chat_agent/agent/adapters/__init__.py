from .cli import CLIAdapter
from .formatting import markdown_to_plaintext
from .gmail import GmailAdapter
from .protocol import ChannelAdapter

__all__ = ["ChannelAdapter", "CLIAdapter", "GmailAdapter", "markdown_to_plaintext"]
