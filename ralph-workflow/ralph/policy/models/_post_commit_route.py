"""PostCommitRoute Pydantic model."""

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel
from ralph.policy.models._post_commit_route_when import PostCommitRouteWhen


class PostCommitRoute(_FrozenPolicyModel):
    """Budget-guarded route applied after commit success."""

    when: PostCommitRouteWhen = Field(..., description="Route condition")
    target: str = Field(..., description="Target phase when condition matches")
