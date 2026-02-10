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
from .m0011_system_prompt_formatting import M0011SystemPromptFormatting
from .m0012_turn_persistence_prompt_tuning import (
    M0012TurnPersistencePromptTuning,
)
from .m0013_memory_writer_pipeline import M0013MemoryWriterPipeline
from .m0014_recent_context_priority import M0014RecentContextPriority
from .m0015_post_review_packet_prompt import M0015PostReviewPacketPrompt
from .m0016_replace_block_prompt_update import M0016ReplaceBlockPromptUpdate
from .m0017_inner_state_discipline import M0017InnerStateDiscipline
from .m0018_trivial_turn_exemption_widen import M0018TrivialTurnExemptionWiden
from .m0019_review_packet_violations import M0019ReviewPacketViolations
from .m0020_empty_reply_violation import M0020EmptyReplyViolation
from .m0021_memory_searcher import M0021MemorySearcher
from .m0022_post_reviewer_zh_tw import M0022PostReviewerZhTw
from .m0023_brain_prompt_zh_tw import M0023BrainPromptZhTw
from .m0024_reviewer_enforcement import M0024ReviewerEnforcement

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
    M0011SystemPromptFormatting(),
    M0012TurnPersistencePromptTuning(),
    M0013MemoryWriterPipeline(),
    M0014RecentContextPriority(),
    M0015PostReviewPacketPrompt(),
    M0016ReplaceBlockPromptUpdate(),
    M0017InnerStateDiscipline(),
    M0018TrivialTurnExemptionWiden(),
    M0019ReviewPacketViolations(),
    M0020EmptyReplyViolation(),
    M0021MemorySearcher(),
    M0022PostReviewerZhTw(),
    M0023BrainPromptZhTw(),
    M0024ReviewerEnforcement(),
]
