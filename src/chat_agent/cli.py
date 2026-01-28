from .core import load_config
from .llm import create_client
from .context import Conversation, ContextBuilder


def main():
    config = load_config()
    brain_config = config.agents["brain"].llm
    client = create_client(brain_config)

    conversation = Conversation()
    builder = ContextBuilder()

    print("Chat started. Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        if user_input.lower() == "quit":
            print("Bye!")
            break

        conversation.add("user", user_input)
        messages = builder.build(conversation)

        try:
            response = client.chat(messages)
        except Exception as e:
            print(f"Error: {e}")
            conversation._messages.pop()  # Remove failed user message
            continue

        conversation.add("assistant", response)
        print(f"Assistant: {response}\n")


if __name__ == "__main__":
    main()
