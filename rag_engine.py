"""
rag_engine.py — Semantic RAG layer over candidate profiles
===========================================================
Uses ChromaDB (in-memory) + sentence-transformers to embed all screened
resumes, then lets the chatbot answer natural-language queries like:
  "show me candidates with PySpark experience who also have education sector exposure"

Public API:
  build_index(screening_results)          -> RAGIndex
  query_index(index, question, top_k=5)   -> list[dict]   (ranked candidate dicts)
  format_rag_context(candidates)          -> str           (compact text for LLM prompt)
  is_rag_query(user_message)              -> bool          (heuristic detector)
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import Optional

# ── Optional heavy imports ────────────────────────────────────────────────────
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    _ST_AVAILABLE = True
except ImportError:
    _ST_AVAILABLE = False

# ── Embedding model (shared with backend if already loaded) ───────────────────
_MODEL_NAME = "all-MiniLM-L6-v2"
_embed_model: Optional[object] = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None and _ST_AVAILABLE:
        try:
            _embed_model = SentenceTransformer(_MODEL_NAME)
        except Exception as e:
            print(f"[RAG] Could not load embedding model: {e}")
    return _embed_model


# ── RAGIndex dataclass ────────────────────────────────────────────────────────

@dataclass
class RAGIndex:
    """Holds the ChromaDB collection + a parallel list of candidate dicts."""
    collection: object                       # chromadb.Collection
    candidates: list[dict] = field(default_factory=list)
    method: str = "chroma"                   # "chroma" | "tfidf_fallback"
    # TF-IDF fallback fields (populated only when Chroma unavailable)
    _tfidf_matrix: object = None
    _tfidf_vect: object = None


def _build_profile_doc(res: dict) -> str:
    """Convert a screening result dict into a rich text document for embedding."""
    parts = [
        f"Candidate: {res.get('name', 'Unknown')}",
        f"Match score: {res.get('score', 0):.1f}%",
        f"Experience: {res.get('exp', 0)} years",
        f"Teaching degree: {'yes' if res.get('has_deg') else 'no'}",
        f"Certification: {'yes' if res.get('has_cert') else 'no'}",
        f"Premier institution: {'yes' if res.get('is_premier') else 'no'}",
        f"Status: {'qualified' if not res.get('filtered') else 'filtered out'}",
    ]
    if res.get("tags"):
        parts.append("Skills: " + ", ".join(res["tags"]))
    if res.get("matched_kw"):
        parts.append("JD keywords matched: " + ", ".join(res["matched_kw"]))
    if res.get("missing_kw"):
        parts.append("JD keywords missing: " + ", ".join(res["missing_kw"]))
    # Full resume text (truncated) — gives semantic richness for niche queries
    full_text = res.get("text", "")
    if full_text:
        parts.append("Resume excerpt: " + full_text[:1200])
    return "\n".join(parts)


def build_index(screening_results: list[dict]) -> Optional[RAGIndex]:
    """
    Embed all candidate profiles and load them into an in-memory ChromaDB collection.
    Falls back to TF-IDF cosine similarity if ChromaDB / sentence-transformers unavailable.
    Returns None if no results to index.
    """
    if not screening_results:
        return None

    docs      = [_build_profile_doc(r) for r in screening_results]
    ids       = [f"cand_{i}" for i in range(len(screening_results))]
    metadatas = [
        {
            "name":       str(r.get("name", "")),
            "score":      float(r.get("score", 0)),
            "exp":        int(r.get("exp", 0)),
            "has_deg":    int(bool(r.get("has_deg"))),
            "has_cert":   int(bool(r.get("has_cert"))),
            "is_premier": int(bool(r.get("is_premier"))),
            "filtered":   int(bool(r.get("filtered"))),
            "rank":       int(r.get("rank", 0)),
        }
        for r in screening_results
    ]

    # ── Try ChromaDB path ──────────────────────────────────────────────────
    if _CHROMA_AVAILABLE:
        model = _get_embed_model()
        if model is not None:
            try:
                embeddings = model.encode(docs, show_progress_bar=False).tolist()
                client = chromadb.Client(ChromaSettings(anonymized_telemetry=False))
                # Fresh ephemeral collection every time (state is per session)
                collection = client.create_collection(
                    name="candidates",
                    metadata={"hnsw:space": "cosine"},
                )
                collection.add(
                    documents=docs,
                    embeddings=embeddings,
                    ids=ids,
                    metadatas=metadatas,
                )
                return RAGIndex(collection=collection, candidates=screening_results, method="chroma")
            except Exception as e:
                print(f"[RAG] ChromaDB build failed ({e}), falling back to TF-IDF")

    # ── TF-IDF fallback ────────────────────────────────────────────────────
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vect   = TfidfVectorizer(ngram_range=(1, 2), max_features=8000)
        matrix = vect.fit_transform(docs)
        idx = RAGIndex(collection=None, candidates=screening_results, method="tfidf_fallback")
        idx._tfidf_matrix = matrix
        idx._tfidf_vect   = vect
        return idx
    except Exception as e:
        print(f"[RAG] TF-IDF fallback also failed: {e}")
        return None


def query_index(index: RAGIndex, question: str, top_k: int = 5) -> list[dict]:
    """
    Semantic search: returns up to top_k candidate dicts ranked by relevance.
    """
    if index is None or not index.candidates:
        return []

    top_k = min(top_k, len(index.candidates))

    # ── ChromaDB path ──────────────────────────────────────────────────────
    if index.method == "chroma" and index.collection is not None:
        model = _get_embed_model()
        if model is not None:
            try:
                q_embed = model.encode([question], show_progress_bar=False).tolist()
                results = index.collection.query(
                    query_embeddings=q_embed,
                    n_results=top_k,
                    include=["metadatas", "distances"],
                )
                # Map back to original candidate dicts
                names = [m["name"] for m in results["metadatas"][0]]
                found = []
                for name in names:
                    for cand in index.candidates:
                        if cand.get("name") == name:
                            found.append(cand)
                            break
                return found
            except Exception as e:
                print(f"[RAG] ChromaDB query failed ({e}), falling back")

    # ── TF-IDF fallback path ───────────────────────────────────────────────
    if index._tfidf_matrix is not None and index._tfidf_vect is not None:
        try:
            from sklearn.metrics.pairwise import cosine_similarity
            import numpy as np
            q_vec = index._tfidf_vect.transform([question])
            sims  = cosine_similarity(q_vec, index._tfidf_matrix).flatten()
            top_i = np.argsort(sims)[::-1][:top_k]
            return [index.candidates[i] for i in top_i if sims[i] > 0]
        except Exception as e:
            print(f"[RAG] TF-IDF query failed: {e}")

    return []


def format_rag_context(candidates: list[dict]) -> str:
    """Compact text block describing the semantically retrieved candidates."""
    if not candidates:
        return "No relevant candidates found for this query."
    lines = ["SEMANTICALLY MATCHED CANDIDATES (ranked by relevance):"]
    for i, r in enumerate(candidates, 1):
        status = "✅ Qualified" if not r.get("filtered") else "❌ Filtered"
        skills = ", ".join(r.get("tags", [])[:6]) or "none"
        matched = ", ".join(r.get("matched_kw", [])[:5]) or "none"
        missing = ", ".join(r.get("missing_kw", [])[:4]) or "none"
        lines.append(
            f"{i}. {r.get('name','?')} [{status}] — Score: {r.get('score',0):.1f}% | "
            f"Exp: {r.get('exp',0)} yr | Degree: {'✅' if r.get('has_deg') else '❌'} | "
            f"Cert: {'✅' if r.get('has_cert') else '❌'} | Premier: {'✅' if r.get('is_premier') else '❌'}\n"
            f"   Skills: {skills}\n"
            f"   JD Matched: {matched} | Missing: {missing}"
        )
    return "\n".join(lines)


# ── Heuristic: detect queries that benefit from semantic search ───────────────

_RAG_SIGNALS = [
    r"\bwho\b.*(experience|skill|background|worked|knows|familiar|proficient)",
    r"\b(find|show|list|give me|identify)\b.*(candidate|person|applicant|resume)",
    r"\bcandidate.*\b(with|who|having)\b",
    r"\bexperience (in|with)\b",
    r"\b(sector|domain|industry|field)\b",
    r"\bskill(s| set)\b",
    r"\b(pyspark|python|java|sql|tableau|excel|curriculum|pedagogy|edtech)\b",
    r"\b(education|school|teaching|classroom|stem|k-12)\b.*\b(sector|exposure|background)\b",
    r"\bboth\b.*\band\b",           # "candidates with X and Y"
    r"\bwho (also|additionally)\b",
]

_RAG_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _RAG_SIGNALS]


def is_rag_query(user_message: str) -> bool:
    """Return True if the message looks like a semantic candidate-search query."""
    return any(p.search(user_message) for p in _RAG_PATTERNS)
