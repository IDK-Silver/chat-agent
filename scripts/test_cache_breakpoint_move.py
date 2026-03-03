"""Test: does moving a cache breakpoint invalidate cache on unchanged blocks?

Uses Claude Haiku 3 (cheapest model, $0.25/MTok base).
Minimum cacheable: 2048 tokens.

Expected result:
- Request 1: all tokens = cache_creation (first time)
- Request 2: block_A = cache_read, block_B = cache_creation
- Request 3: block_A + block_B = cache_read, block_C = cache_creation
"""

import os
import time

import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-haiku-4-5-20251001"

# ~5000 tokens of filler (above 4096 minimum for Haiku 4.5)
BLOCK_A = "The following is a reference document about marine biology. " + " ".join(
    [
        f"Fact {i}: Marine species number {i} is found in the Pacific Ocean "
        f"at depths between {i * 10} and {i * 10 + 50} meters, "
        f"feeding primarily on plankton and small crustaceans. "
        f"Its population has been studied since {1900 + i} "
        f"and shows seasonal migration patterns."
        for i in range(1, 301)
    ]
)

# ~600 tokens of additional content
BLOCK_B = "Additional findings from 2024 research. " + " ".join(
    [
        f"Study {i}: Researchers at University {i} discovered that "
        f"species {i + 150} exhibits bioluminescence at {i * 5} meters depth."
        for i in range(1, 41)
    ]
)

# ~600 tokens more
BLOCK_C = "Latest 2025 expedition results. " + " ".join(
    [
        f"Expedition {i}: The vessel Discovery-{i} mapped coral formations "
        f"in sector {i} revealing {i * 3} previously unknown species."
        for i in range(1, 41)
    ]
)

QUESTION = "How many marine species are mentioned? Answer in one sentence."


def print_usage(label: str, usage: anthropic.types.Usage) -> None:
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    regular = usage.input_tokens
    total = read + creation + regular
    print(f"\n=== {label} ===")
    print(f"  cache_read:     {read:>6} tokens  (0.1x)")
    print(f"  cache_creation: {creation:>6} tokens  (write)")
    print(f"  input_tokens:   {regular:>6} tokens  (1.0x)")
    print(f"  total_input:    {total:>6} tokens")
    print(f"  output:         {usage.output_tokens:>6} tokens")


def make_request(label: str, system_blocks: list[dict], wait_before: float = 0) -> None:
    if wait_before > 0:
        print(f"\n  (waiting {wait_before}s for cache to be available...)")
        time.sleep(wait_before)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=50,
        system=system_blocks,
        messages=[{"role": "user", "content": QUESTION}],
    )
    print_usage(label, resp.usage)


# --- Request 1: [block_A with BP] ---
print("=" * 60)
print("Test: Moving cache breakpoint across requests")
print("=" * 60)

make_request(
    "Request 1: [block_A BP]",
    [
        {
            "type": "text",
            "text": BLOCK_A,
            "cache_control": {"type": "ephemeral"},
        },
    ],
)

# --- Request 2: [block_A] [block_B with BP] ---
make_request(
    "Request 2: [block_A] [block_B BP]  -- BP moved, block_A unchanged",
    [
        {
            "type": "text",
            "text": BLOCK_A,
        },
        {
            "type": "text",
            "text": BLOCK_B,
            "cache_control": {"type": "ephemeral"},
        },
    ],
    wait_before=3,
)

# --- Request 3: [block_A] [block_B] [block_C with BP] ---
make_request(
    "Request 3: [block_A] [block_B] [block_C BP]  -- BP moved again",
    [
        {
            "type": "text",
            "text": BLOCK_A,
        },
        {
            "type": "text",
            "text": BLOCK_B,
        },
        {
            "type": "text",
            "text": BLOCK_C,
            "cache_control": {"type": "ephemeral"},
        },
    ],
    wait_before=3,
)

print("\n" + "=" * 60)
print("Expected: cache_read grows with each request as old blocks hit cache")
print("=" * 60)
