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
from .m0025_remove_editor_llm import M0025RemoveEditorLlm
from .m0026_label_requires_persistence import M0026LabelRequiresPersistence
from .m0027_memory_search_no_index_results import M0027MemorySearchNoIndexResults
from .m0028_memory_edit_v2_intent_pipeline import M0028MemoryEditV2IntentPipeline
from .m0029_post_reviewer_label_stability import M0029PostReviewerLabelStability
from .m0030_strict_target_anomaly_signals import M0030StrictTargetAnomalySignals
from .m0031_memory_search_two_stage_configurable_limits import (
    M0031MemorySearchTwoStageConfigurableLimits,
)
from .m0032_delete_file_index_sync import M0032DeleteFileIndexSync
from .m0033_memory_search_zh_tw import M0033MemorySearchZhTw
from .m0034_memory_edit_ordering_rule import M0034MemoryEditOrderingRule
from .m0035_scope_boundary_prompts import M0035ScopeBoundaryPrompts
from .m0036_memory_short_term_move import M0036MemoryShortTermMove
from .m0037_context_window_boot import M0037ContextWindowBoot
from .m0038_skills_first_shell import M0038SkillsFirstShell
from .m0039_long_term_memory import M0039LongTermMemory
from .m0040_persona_trigger import M0040PersonaTrigger
from .m0041_memory_edit_overwrite import M0041MemoryEditOverwrite
from .m0042_vision_agent import M0042VisionAgent
from .m0043_people_folder import M0043PeopleFolder
from .m0044_people_search_trigger import M0044PeopleSearchTrigger
from .m0045_multi_intent_preference import M0045MultiIntentPreference
from .m0046_gui_agents import M0046GuiAgents
from .m0047_session_reorganize import M0047SessionReorganize
from .m0048_gui_report_problem import M0048GuiReportProblem
from .m0049_gui_resume_state import M0049GuiResumeState
from .m0050_brain_screenshot import M0050BrainScreenshot
from .m0051_gui_obstacle_awareness import M0051GuiObstacleAwareness
from .m0052_gui_unautomatable_escalation import M0052GuiUnautomatableEscalation
from .m0053_gui_scroll_awareness import M0053GuiScrollAwareness
from .m0054_gui_human_browsing import M0054GuiHumanBrowsing
from .m0055_gui_force_tool_call import M0055GuiForceToolCall
from .m0056_brain_tool_immediate import M0056BrainToolImmediate
from .m0057_gui_right_click_maximize import M0057GuiRightClickMaximize
from .m0058_gui_scroll_keys import M0058GuiScrollKeys
from .m0059_gui_prompt_rewrite import M0059GuiPromptRewrite
from .m0060_brain_prompt_v2 import M0060BrainPromptV2

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
    M0025RemoveEditorLlm(),
    M0026LabelRequiresPersistence(),
    M0027MemorySearchNoIndexResults(),
    M0028MemoryEditV2IntentPipeline(),
    M0029PostReviewerLabelStability(),
    M0030StrictTargetAnomalySignals(),
    M0031MemorySearchTwoStageConfigurableLimits(),
    M0032DeleteFileIndexSync(),
    M0033MemorySearchZhTw(),
    M0034MemoryEditOrderingRule(),
    M0035ScopeBoundaryPrompts(),
    M0036MemoryShortTermMove(),
    M0037ContextWindowBoot(),
    M0038SkillsFirstShell(),
    M0039LongTermMemory(),
    M0040PersonaTrigger(),
    M0041MemoryEditOverwrite(),
    M0042VisionAgent(),
    M0043PeopleFolder(),
    M0044PeopleSearchTrigger(),
    M0045MultiIntentPreference(),
    M0046GuiAgents(),
    M0047SessionReorganize(),
    M0048GuiReportProblem(),
    M0049GuiResumeState(),
    M0050BrainScreenshot(),
    M0051GuiObstacleAwareness(),
    M0052GuiUnautomatableEscalation(),
    M0053GuiScrollAwareness(),
    M0054GuiHumanBrowsing(),
    M0055GuiForceToolCall(),
    M0056BrainToolImmediate(),
    M0057GuiRightClickMaximize(),
    M0058GuiScrollKeys(),
    M0059GuiPromptRewrite(),
    M0060BrainPromptV2(),
]
