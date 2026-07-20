"""Time-related tools."""

from datetime import datetime

from ...llm.schema import ToolDefinition, ToolParameter
from ...timezone_utils import get_spec, now as tz_now, parse_timezone_spec


def get_current_time(timezone: str | None = None) -> str:
    """Get the current time in the specified timezone."""
    try:
        if timezone:
            tz = parse_timezone_spec(timezone)
            now = datetime.now(tz)
            return now.strftime(f"%Y-%m-%d %H:%M:%S {timezone}")
        # Default: use app timezone
        tz_label = get_spec()
        return tz_now().strftime(f"%Y-%m-%d %H:%M:%S {tz_label}")
    except Exception as e:
        return f"Error getting time for timezone '{timezone}': {e}"


GET_CURRENT_TIME_DEFINITION = ToolDefinition(
    name="get_current_time",
    description="Get the current date and time in a specified timezone.",
    parameters={
        "timezone": ToolParameter(
            type="string",
            description=(
                "Timezone spec (e.g., 'UTC+8', 'UTC+08:00', or "
                "'Asia/Taipei'). Defaults to app timezone."
            ),
        ),
    },
    required=[],
)
