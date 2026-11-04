"""AIAD benchmark agent-loop implementation.

The package records individual trials only. It does not calculate benchmark
aggregates or release decisions.
"""

from .artifacts import StagedArtifact, UnsafeArtifactPath, stage_claimed_artifacts
from .cli import TrialLaunch
from .loop import run_agent_loop
from .mcp_bridge import ClientSessionBridge
from .providers import AnthropicMessagesAdapter, OpenAIResponsesAdapter
from .types import (
    IsolationPreflight,
    PublicStageProjection,
    RunConfig,
    TrialResult,
)

__all__ = [
    "AnthropicMessagesAdapter",
    "ClientSessionBridge",
    "IsolationPreflight",
    "OpenAIResponsesAdapter",
    "PublicStageProjection",
    "RunConfig",
    "StagedArtifact",
    "TrialLaunch",
    "TrialResult",
    "UnsafeArtifactPath",
    "run_agent_loop",
    "stage_claimed_artifacts",
]
