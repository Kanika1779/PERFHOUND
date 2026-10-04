"""RAG: retrieve the parts of a project relevant to a performance symptom.

v1: commit documents + BM25 lexical retrieval.
v2: dense embeddings (fastembed) + hybrid via Reciprocal Rank Fusion.
Evaluated by recall@k on cases with a known culprit (scripts/eval_retrieval.py).
"""

from .bm25 import BM25
from .documents import CommitDocument, build_commit_documents, build_query
from .retriever import RankedCandidate, rank_candidates, rank_dense, rank_hybrid
from .tokenize import tokenize

__all__ = ["BM25", "CommitDocument", "build_commit_documents", "build_query", "RankedCandidate",
           "rank_candidates", "rank_dense", "rank_hybrid", "tokenize"]
