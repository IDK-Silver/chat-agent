"""Workspace initialization command."""

from rich.console import Console

from ..context import ContextBuilder, Conversation
from ..core import load_config
from ..llm import create_agent_client
from ..llm.schema import Message
from ..tools import (
    ToolRegistry,
    ShellExecutor,
    READ_FILE_DEFINITION,
    WRITE_FILE_DEFINITION,
    EDIT_FILE_DEFINITION,
    EXECUTE_SHELL_DEFINITION,
    create_read_file,
    create_write_file,
    create_edit_file,
    create_execute_shell,
)
from ..workspace import WorkspaceManager, WorkspaceInitializer
from .console import ChatConsole


def _setup_tools(config) -> ToolRegistry:
    """Set up tools for init agent."""
    registry = ToolRegistry()
    agent_os_dir = config.get_agent_os_dir()
    allowed_paths = [str(agent_os_dir)]
    tools_config = config.tools

    # Shell
    executor = ShellExecutor(
        agent_os_dir=agent_os_dir,
        blacklist=tools_config.shell.blacklist,
        timeout=tools_config.shell.timeout,
    )
    registry.register(
        "execute_shell",
        create_execute_shell(executor),
        EXECUTE_SHELL_DEFINITION,
    )

    # File tools
    registry.register(
        "read_file",
        create_read_file(allowed_paths, agent_os_dir),
        READ_FILE_DEFINITION,
    )
    registry.register(
        "write_file",
        create_write_file(allowed_paths, agent_os_dir),
        WRITE_FILE_DEFINITION,
    )
    registry.register(
        "edit_file",
        create_edit_file(allowed_paths, agent_os_dir),
        EDIT_FILE_DEFINITION,
    )

    return registry


def _run_init_agent(config, workspace: WorkspaceManager) -> None:
    """Run init agent conversation to set up persona."""
    if "init" not in config.agents:
        raise ValueError("agents.init not configured. Add it to config to use init agent.")

    init_agent_config = config.agents["init"]
    client = create_agent_client(
        init_agent_config,
        retry_label="init",
    )

    system_prompt = workspace.get_system_prompt("init")

    console = ChatConsole()
    prompt_console = Console()
    conversation = Conversation()
    builder = ContextBuilder(system_prompt=system_prompt)
    registry = _setup_tools(config)

    console.print_info("Starting persona setup. Type /exit to exit.\n")

    while True:
        try:
            user_input = prompt_console.input("> ")
        except (EOFError, KeyboardInterrupt):
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input.lower() in ("/exit", "/quit", "/q"):
            break

        conversation.add("user", user_input)
        messages = builder.build(conversation)

        try:
            tools = registry.get_definitions()

            with console.spinner():
                response = client.chat_with_tools(messages, tools)

            while response.has_tool_calls():
                conversation.add_assistant_with_tools(
                    response.content,
                    response.tool_calls,
                    reasoning_content=response.reasoning_content,
                    reasoning_details=response.reasoning_details,
                )

                for tool_call in response.tool_calls:
                    console.print_tool_call(tool_call)
                    with console.spinner("Executing..."):
                        result = registry.execute(tool_call)
                    console.print_tool_result(tool_call, result.content)
                    conversation.add_tool_result(tool_call.id, tool_call.name, result.content)

                messages = builder.build(conversation)
                with console.spinner():
                    response = client.chat_with_tools(messages, tools)

            final_content = response.content or ""
            if not final_content.strip():
                messages = builder.build(conversation)
                with console.spinner():
                    final_content = client.chat_with_tools(messages, []).content or ""

            if not final_content.strip():
                finalize_messages = [
                    *builder.build(conversation),
                    Message(
                        role="user",
                        content=(
                            "FINALIZATION STEP: provide the final user-facing reply now. "
                            "Do not call tools."
                        ),
                    ),
                ]
                with console.spinner():
                    final_content = (
                        client.chat_with_tools(finalize_messages, []).content or ""
                    )

            if not final_content.strip():
                raise RuntimeError(
                    "Model returned empty final response during init flow."
                )

            conversation.add("assistant", final_content)
            console.print_assistant(final_content)

        except Exception as e:
            console.print_error(str(e))
            conversation.truncate_to(max(len(conversation) - 1, 0))
            continue

    console.print_info("Persona setup complete.")

def init_command() -> None:
    """Initialize workspace directory and run init agent."""
    console = Console()

    config = load_config()
    agent_os_dir = config.get_agent_os_dir()

    from ..timezone_utils import configure_runtime_timezone
    configure_runtime_timezone(config.app.timezone)

    console.print(f"[blue]Initializing workspace at:[/blue] {agent_os_dir}")

    manager = WorkspaceManager(agent_os_dir)
    initializer = WorkspaceInitializer(manager)

    if manager.is_initialized():
        version = manager.get_kernel_version()
        console.print(f"[yellow]Workspace already initialized (v{version})[/yellow]")

        if initializer.needs_upgrade():
            console.print("[yellow]Kernel upgrade available[/yellow]")
            if _confirm(console, "Upgrade kernel? (memory will be preserved)"):
                initializer.upgrade_kernel()
                console.print("[green]Kernel upgraded successfully[/green]")

        # Offer to re-run init agent
        if "init" in config.agents and _confirm(console, "Re-run persona setup?"):
            _run_init_agent(config, manager)
        return

    # Create workspace structure
    initializer.create_structure()
    console.print("[green]Workspace created successfully[/green]\n")

    # Run init agent if configured
    if "init" in config.agents:
        _run_init_agent(config, manager)
    else:
        console.print("[yellow]agents.init not configured, skipping persona setup.[/yellow]")
        console.print("[dim]Add agents.init to config to enable guided persona setup.[/dim]")


def _confirm(console: Console, message: str) -> bool:
    """Ask for confirmation."""
    response = console.input(f"{message} [y/N] ")
    return response.lower() in ("y", "yes")
