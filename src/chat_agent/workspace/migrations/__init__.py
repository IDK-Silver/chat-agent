"""Kernel migrations registry."""

from .m0001_initial import M0001Initial
from .m0002_agents_structure import M0002AgentsStructure

ALL_MIGRATIONS = [
    M0001Initial(),
    M0002AgentsStructure(),
]
