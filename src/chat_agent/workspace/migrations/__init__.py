"""Kernel migrations registry."""

from .m0001_initial import M0001Initial
from .m0002_agents_structure import M0002AgentsStructure
from .m0003_prompt_v3 import M0003PromptV3
from .m0004_shutdown_v2 import M0004ShutdownV2
from .m0005_reviewer_prompts import M0005ReviewerPrompts
from .m0006_reviewer_agents import M0006ReviewerAgents
from .m0007_post_reviewer_prompt_tuning import M0007PostReviewerPromptTuning
from .m0008_post_reviewer_structured_actions import (
    M0008PostReviewerStructuredActions,
)
from .m0009_shutdown_reviewer_prompt import M0009ShutdownReviewerPrompt
from .m0010_reviewer_parse_retry_prompts import M0010ReviewerParseRetryPrompts

ALL_MIGRATIONS = [
    M0001Initial(),
    M0002AgentsStructure(),
    M0003PromptV3(),
    M0004ShutdownV2(),
    M0005ReviewerPrompts(),
    M0006ReviewerAgents(),
    M0007PostReviewerPromptTuning(),
    M0008PostReviewerStructuredActions(),
    M0009ShutdownReviewerPrompt(),
    M0010ReviewerParseRetryPrompts(),
]
