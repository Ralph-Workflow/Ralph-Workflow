
from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel

_FROZEN = ConfigDict(frozen=True)


class CommitState(RalphBaseModel):

    model_config = _FROZEN

    message_prepared: bool = False
    diff_prepared: bool = False
    agent_invoked: bool = False
