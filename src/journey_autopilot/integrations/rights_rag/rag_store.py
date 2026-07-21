import chromadb
from chromadb.utils import embedding_functions
from pathlib import Path
import os

# Package root is two levels up (integrations/rights_rag/ -> journey_autopilot/);
# keep the Chroma store under the package data dir, consistent with config.py.
BASE_DIR = Path(__file__).resolve().parents[2]
CHROMA_PATH = Path(os.getenv("CHROMA_PATH", str(BASE_DIR / "data" / "chromadb")))

# Multilingual model because of German texts
EMBEDDING_MODEL = "paraphrase-multilingual-mpnet-base-v2"

# Cosine-distance cutoff for "close enough to be worth citing". Calibrated
# against this (narrow, fahrgastrechte-only) corpus: on-topic queries land
# around 0.18-0.23, a clearly off-topic query (e.g. bike storage) around
# 0.48-0.52 — 0.3 sits in the gap between the two.
MAX_RELEVANCE_DISTANCE = 0.3

# Substrings that identify the bahn.de clause matching each ticket type. Used
# as a content pre-filter (Chroma ``where_document``) so semantic search only
# ranks chunks that literally carry the right ticket-type language. Without it
# the nearest neighbour is almost always the generic "ab 60 Minuten"/Zeitkarten
# passage — it shares the compensation vocabulary with every query and so wins
# the ranking even for a single ticket it does not apply to. Case-sensitive
# (Chroma ``$contains`` is), so the strings match the crawled corpus casing.
# einzelticket also covers bc25/bc50: the single-ticket percentage rule applies
# to them (compensation is on the price actually paid) — see rights_service.
_TICKET_TYPE_KEYWORDS: dict[str, list[str]] = {
    "einzelticket": ["des gezahlten Fahrpreises"],
    "zeitkarte_fv": ["Zeitfahrkarten des Fernverkehrs", "Zeitkarten des Fernverkehrs"],
    "zeitkarte_nv": ["Zeitfahrkarten des Nahverkehrs", "Zeitkarten des Nahverkehrs"],
    "bc100": ["BahnCard 100"],
    # The Deutschland-Ticket's compensation is the Nahverkehr-Zeitkarte rule, so
    # cite that clause too, not just chunks that name the ticket in passing.
    "deutschland_ticket": ["Deutschland-Ticket", "Zeitfahrkarten des Nahverkehrs"],
}


def _ticket_type_filter(ticket_type: str) -> dict | None:
    """Chroma ``where_document`` clause restricting retrieval to the chunks
    that carry this ticket type's language, or ``None`` for an unknown type."""
    keywords = _TICKET_TYPE_KEYWORDS.get(ticket_type)
    if not keywords:
        return None
    if len(keywords) == 1:
        return {"$contains": keywords[0]}
    return {"$or": [{"$contains": kw} for kw in keywords]}


def _build_embedding_fn():
    """Load the embedding model, preferring the local Hugging Face cache.

    ``local_files_only=True`` skips the hub's online revalidation (which looks
    like a re-download and stalls the first answer on slow networks). On the
    first-ever run — model not cached yet — or with an older
    sentence-transformers that lacks the flag, fall back to a normal load.
    """
    try:
        return embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL, local_files_only=True
        )
    except Exception:
        return embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL
        )


class FahrgastrechteRAG:

    def __init__(self):
        self.client = chromadb.PersistentClient(path=str(CHROMA_PATH))

        self.embedding_fn = _build_embedding_fn()
        
        self.collection = self.client.get_or_create_collection(
            name="fahrgastrechte",
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )
    
    # -------------------------
    # INDEXING (daily)
    # -------------------------

    def rebuild_index(self, documents: list[dict]):
        """Delete the old index and rebuild it from scratch"""

        # Delete old data so nothing stays outdated
        try:
            self.client.delete_collection("fahrgastrechte")
        except:
            pass
        
        self.collection = self.client.get_or_create_collection(
            name="fahrgastrechte",
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )
        
        # Chunk and store the documents
        all_chunks = []
        all_ids = []
        all_metadata = []
        
        for doc in documents:
            chunks = self._chunk_text(doc["content"])
            for i, chunk in enumerate(chunks):
                all_chunks.append(chunk)
                all_ids.append(f"{doc['source']}__chunk_{i}")
                all_metadata.append({
                    "source": doc["source"],
                    "crawled_at": doc["crawled_at"],
                    "chunk_index": i
                })
        
        # Store in ChromaDB (computes embeddings automatically)
        self.collection.add(
            documents=all_chunks,
            ids=all_ids,
            metadatas=all_metadata
        )
        
        print(f"Index built: {len(all_chunks)} chunks from {len(documents)} pages")
    
    def _chunk_text(self, text: str, chunk_size: int = 400) -> list[str]:
        """Split text at paragraph boundaries, not mid-sentence"""
        paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 40]
        
        chunks = []
        current = ""
        
        for para in paragraphs:
            if len(current) + len(para) <= chunk_size:
                current += para + "\n\n"
            else:
                if current.strip():
                    chunks.append(current.strip())
                current = para + "\n\n"
        
        if current.strip():
            chunks.append(current.strip())
        
        return chunks
    
    # -------------------------
    # QUERY (on every delay)
    # -------------------------

    def retrieve(
        self,
        query: str,
        n_results: int = 1,
        max_distance: float = MAX_RELEVANCE_DISTANCE,
        where_document: dict | None = None,
    ) -> list[dict]:
        """
        Finds the most relevant chunk(s) for a query.
        Runs in ~50ms because ChromaDB is local.

        ``where_document`` (a Chroma content filter) narrows the candidate set
        to chunks that literally contain the caller's required language before
        semantic ranking — the difference between citing this ticket type's
        clause and citing whichever passage happens to share the most
        compensation vocabulary.

        Chroma returns ``n_results`` matches regardless of fit, so a chunk
        that only vaguely relates to the query would otherwise be cited just
        as confidently as a genuinely on-topic one. Dropping anything past
        ``max_distance`` (cosine distance — lower is closer) means an empty
        result is possible and expected when nothing in the index is
        actually close to the query, not just when the index is empty.

        Returns ``[{"text": ..., "source": <bahn.de URL>}, ...]`` so callers
        can cite where a chunk came from, not just its text.
        """
        query_kwargs: dict = {"query_texts": [query], "n_results": n_results}
        if where_document:
            query_kwargs["where_document"] = where_document
        results = self.collection.query(**query_kwargs)

        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results["distances"][0]
        return [
            {"text": doc, "source": meta.get("source")}
            for doc, meta, dist in zip(docs, metas, dists)
            if dist <= max_distance
        ]

    def retrieve_for_case(
        self,
        delay_minutes: int,
        ticket_type: str,
        bahncard_type: str,
        n_results: int = 1,
    ) -> list[dict]:
        query = (
            f"compensation delay {delay_minutes} minutes "
            f"ticket type: {ticket_type} "
            f"BahnCard: {bahncard_type}"
        )
        return self.retrieve(
            query,
            n_results=n_results,
            where_document=_ticket_type_filter(ticket_type),
        )
