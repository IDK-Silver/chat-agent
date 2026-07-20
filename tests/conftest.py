"""Global test fixtures."""

from lincy.timezone_utils import configure as configure_tz

# Configure app timezone once for all tests
configure_tz("UTC+8")
