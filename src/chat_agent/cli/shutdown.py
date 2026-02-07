"""Graceful shutdown with LLM memory saving."""

from ..context import ContextBuilder, Conversation
from ..llm.base import LLMClient
from ..tools import ToolRegistry
from ..workspace import WorkspaceManager
from .console import ChatConsole

_MAX_TOOL_ITERATIONS = 20


def _has_conversation_content(conversation: Conversation) -> bool:
    """Check if conversation has any user messages."""
    return any(m.role == "user" for m in conversation.get_messages())


def _get_last_user_timestamp(conversation: Conversation):
    """Return timestamp of the latest user message, if any."""
    for message in reversed(conversation.get_messages()):
        if message.role == "user":
            return message.timestamp
    return None


def perform_shutdown(
    client: LLMClient,
    conversation: Conversation,
    builder: ContextBuilder,
    registry: ToolRegistry,
    console: ChatConsole,
    workspace: WorkspaceManager,
    user_id: str,
) -> bool:
    """Send shutdown prompt to LLM and execute tool calls for memory saving.

    Returns:
        True if completed, False if interrupted by KeyboardInterrupt.
    """
    try:
        shutdown_prompt = workspace.get_agent_prompt(
            "brain", "shutdown", current_user=user_id
        )
    except FileNotFoundError:
        return True

    # Keep shutdown-triggered prompt on the same user-time anchor as
    # the latest real user input, so archives don't shift to quit time.
    conversation.add(
        "user",
        shutdown_prompt,
        timestamp=_get_last_user_timestamp(conversation),
    )
    messages = builder.build(conversation)
    tools = registry.get_definitions()

    try:
        with console.spinner("Saving memories..."):
            response = client.chat_with_tools(messages, tools)

        iterations = 0
        while response.has_tool_calls() and iterations < _MAX_TOOL_ITERATIONS:
            iterations += 1

            conversation.add_assistant_with_tools(
                response.content, response.tool_calls
            )

            for tool_call in response.tool_calls:
                console.print_tool_call(tool_call)
                with console.spinner("Executing..."):
                    result = registry.execute(tool_call)
                console.print_tool_result(tool_call, result)
                conversation.add_tool_result(
                    tool_call.id, tool_call.name, result
                )

            messages = builder.build(conversation)
            with console.spinner("Saving memories..."):
                response = client.chat_with_tools(messages, tools)

        if response.content:
            conversation.add("assistant", response.content)

        return True

    except KeyboardInterrupt:
        return False
