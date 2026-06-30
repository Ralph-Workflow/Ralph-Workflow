"""Phase context passed to every phase handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import NotRequired, TypedDict, Unpack

    from rich.console import Console

    from ralph.agents.chain import ChainManager
    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import UnifiedConfig
    from ralph.policy.models import (
        AgentsPolicy,
        ArtifactsPolicy,
        PipelinePolicy,
    )
    from ralph.workspace.protocol import Workspace

    class _PhaseContextArgs(TypedDict):
        workspace: Workspace
        registry: AgentRegistry
        chain_manager: ChainManager
        pipeline_policy: PipelinePolicy
        agents_policy: AgentsPolicy
        artifacts_policy: ArtifactsPolicy
        config: NotRequired[UnifiedConfig | None]
        console: NotRequired[Console | None]


@dataclass(frozen=True)
class PhaseContext:
    """Context passed to every phase handler.

    Attributes:
        workspace: Workspace for file I/O.
        registry: Agent registry for looking up agent configs.
        chain_manager: Chain manager for drain-to-chain resolution.
        pipeline_policy: Pipeline policy (phase graph).
        agents_policy: Agents policy (chains and drain bindings).
        artifacts_policy: Artifacts policy (artifact contracts).
        config: Optional legacy unified config for backward compatibility.
        console: Rich console for output (optional).
    """

    workspace: Workspace
    registry: AgentRegistry
    chain_manager: ChainManager
    pipeline_policy: PipelinePolicy
    agents_policy: AgentsPolicy
    artifacts_policy: ArtifactsPolicy
    config: UnifiedConfig | None = None
    console: Console | None = None

    @classmethod
    def construct(cls, **kwargs: Unpack[_PhaseContextArgs]) -> PhaseContext:
        """Construct a ``PhaseContext`` from a typed-argument mapping.

        This classmethod is the sanctioned public constructor for
        :class:`PhaseContext`; it accepts the same fields as
        :class:`PhaseContext` itself, validates them against the
        :data:`_PhaseContextArgs` TypedDict, and returns a frozen instance.

        Args:
            **kwargs: Field values to forward to :class:`PhaseContext`. The
                accepted keys are documented on :data:`_PhaseContextArgs`:
                ``workspace``, ``registry``, ``chain_manager``,
                ``pipeline_policy``, ``agents_policy``,
                ``artifacts_policy``, plus the optional ``config`` and
                ``console``.

        Returns:
            PhaseContext: A new frozen :class:`PhaseContext` populated from
            ``kwargs``.

        Raises:
            TypeError: If a key in ``kwargs`` is not a known
                :class:`PhaseContext` field.
        """
        return cls(**kwargs)

    @classmethod
    def model_construct(cls, **kwargs: Unpack[_PhaseContextArgs]) -> PhaseContext:
        """Construct a ``PhaseContext`` mirroring pydantic's ``model_construct``.

        This classmethod exists so factories that build pydantic-style
        objects (skipping validation by design) can construct a
        :class:`PhaseContext` through the same name pattern. It is
        behaviorally identical to :meth:`construct`: it forwards
        ``kwargs`` unchanged.

        Args:
            **kwargs: Field values to forward to :class:`PhaseContext`;
                see :meth:`construct` for the accepted key list.

        Returns:
            PhaseContext: A new frozen :class:`PhaseContext` populated from
            ``kwargs``.
        """
        return cls.construct(**kwargs)
