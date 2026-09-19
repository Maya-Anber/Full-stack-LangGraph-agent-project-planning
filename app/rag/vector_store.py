"""In-memory RAG index using local MiniLM embeddings with a TF-IDF fallback."""
from dataclasses import dataclass
from typing import List

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity


@dataclass
class ScoredItem:
    id: int
    title: str
    content: str
    category: str
    score: float


class VectorStore:
    """In-memory TF-IDF index over KnowledgeItem rows."""

    def __init__(self):
        self._vectorizer = None
        self._embedder = None
        self._backend = "tfidf"
        self._matrix = None
        self._items = []  # list of dicts: id, title, content, category

    def build(self, items: List[dict], backend="minilm", model_name="all-MiniLM-L6-v2"):
        """(Re)build the index from a list of {id, title, content, category} dicts."""
        self._items = items
        self._backend = backend
        if not items:
            self._vectorizer = None
            self._embedder = None
            self._matrix = None
            return

        corpus = [f"{it['title']}. {it['content']}" for it in items]
        if backend == "minilm":
            try:
                from sentence_transformers import SentenceTransformer

                self._embedder = SentenceTransformer(model_name)
                self._matrix = self._embedder.encode(corpus, normalize_embeddings=True)
                self._vectorizer = None
                return
            except Exception:
                self._backend = "tfidf"

        from sklearn.feature_extraction.text import TfidfVectorizer

        # max_df prunes terms that appear in almost every document. With only a
        # couple of documents that would prune everything (scikit-learn raises
        # "max_df corresponds to < documents than min_df"), so the threshold is
        # relaxed for tiny corpora -- e.g. a knowledge base edited down to a
        # single entry from the dashboard.
        max_df = 0.95 if len(items) >= 3 else 1.0
        self._vectorizer = TfidfVectorizer(
            stop_words="english",
            ngram_range=(1, 2),
            max_df=max_df,
            min_df=1,
        )
        self._matrix = self._vectorizer.fit_transform(corpus)

    def is_empty(self):
        return self._matrix is None or self._matrix.shape[0] == 0

    def search(self, query: str, top_k: int = 4, min_score: float = 0.0) -> List[ScoredItem]:
        if self.is_empty() or not query.strip():
            return []

        if self._backend == "minilm" and self._embedder is not None:
            query_vec = self._embedder.encode([query], normalize_embeddings=True)
        else:
            query_vec = self._vectorizer.transform([query])
        sims = cosine_similarity(query_vec, self._matrix)[0]
        ranked_idx = np.argsort(sims)[::-1][:top_k]

        results = []
        for idx in ranked_idx:
            score = float(sims[idx])
            if score < min_score:
                continue
            item = self._items[idx]
            results.append(
                ScoredItem(
                    id=item["id"],
                    title=item["title"],
                    content=item["content"],
                    category=item["category"],
                    score=score,
                )
            )
        return results


# Module-level singleton used across the app (rebuilt on startup and on every
# knowledge-base write). See app/rag/ingest.py for mutation entrypoints.
vector_store = VectorStore()
