"""Gateway: the single entry point to git / GitHub data.

Every other Perfhound module asks the gateway for commit data and never
calls git or GitHub directly (Facade + Adapter patterns).
"""

from .api import Gateway
from .cases import BenchmarkSpec, Observation, RegressionCase
from .errors import GatewayError
from .fetcher import FetchError, RepoFetcher
from .models import CandidateCommit, FileChange, PRInfo
from .range import CommitRange
from .snapshot import Snapshot, SnapshotError

__all__ = [
    "BenchmarkSpec", "Observation", "RegressionCase", "FetchError", "RepoFetcher",
    "Gateway", "GatewayError", "CandidateCommit", "FileChange", "PRInfo", "CommitRange", "Snapshot", "SnapshotError"]
