from pydantic import Field

from ralph.policy.models._post_commit_route_when import PostCommitRouteWhen
from ralph.pydantic_compat import RalphBaseModel


class PostCommitRoute(RalphBaseModel):
    when: PostCommitRouteWhen = Field(..., description="Route condition")
    target: str = Field(..., description="Target phase when condition matches")
