"""Time-related tools."""

from datetime import datetime
from zoneinfo import ZoneInfo

from ...llm.schema import ToolDefinition, ToolParameter


def get_current_time(timezone: str = "UTC") -> str:
    """Get the current time in the specified timezone."""
    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        return now.strftime(f"%Y-%m-%d %H:%M:%S {timezone}")
    except Exception as e:
        return f"Error getting time for timezone '{timezone}': {e}"


GET_CURRENT_TIME_DEFINITION = ToolDefinition(
    name="get_current_time",
    description="Get the current date and time in a specified timezone.",
    parameters={
        "timezone": ToolParameter(
            type="string",
            description="The IANA timezone name (e.g., 'UTC', 'Asia/Taipei', 'America/New_York'). Defaults to 'UTC'.",
        ),
    },
    required=[],
)
