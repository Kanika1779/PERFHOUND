"""Gateway: the single entry point to git / GitHub data.

Every other Perfhound module asks the gateway for commit data and never
calls git or GitHub directly (Facade + Adapter patterns).
"""

from .api import Gateway
from .errors import GatewayError
from .models import CandidateCommit, FileChange, PRInfo
from .range import CommitRange

__all__ = ["Gateway", "GatewayError", "CandidateCommit", "FileChange", "PRInfo", "CommitRange"]
