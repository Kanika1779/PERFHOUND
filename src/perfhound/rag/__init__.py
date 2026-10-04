"""RAG: retrieve the parts of a project relevant to a performance symptom.

v1 (this package): commit documents + BM25 lexical retrieval, evaluated by
recall@k on cases with a known culprit. Embeddings / hybrid come in v2.
"""

from .bm25 import BM25
from .documents import CommitDocument, build_commit_documents, build_query
from .retriever import RankedCandidate, rank_candidates
from .tokenize import tokenize

__all__ = ["BM25", "CommitDocument", "build_commit_documents", "build_query", "RankedCandidate",
           "rank_candidates", "tokenize"]
