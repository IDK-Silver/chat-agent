"""Kernel migrations registry."""

from .m0001_initial import M0001Initial
from .m0002_agents_structure import M0002AgentsStructure
from .m0003_prompt_v3 import M0003PromptV3
from .m0004_shutdown_v2 import M0004ShutdownV2

ALL_MIGRATIONS = [
    M0001Initial(),
    M0002AgentsStructure(),
    M0003PromptV3(),
    M0004ShutdownV2(),
]
