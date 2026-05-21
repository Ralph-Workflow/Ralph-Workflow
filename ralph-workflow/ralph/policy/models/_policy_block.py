"""Shared type alias for policy block models."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ralph.policy.models._group_policy_block import GroupPolicyBlock
from ralph.policy.models._individual_policy_block import IndividualPolicyBlock

type PolicyBlock = Annotated[
    GroupPolicyBlock | IndividualPolicyBlock,
    Field(discriminator="kind"),
]
