import chromadb
from chromadb.utils import embedding_functions
from pathlib import Path
import os

# Package root is two levels up (integrations/rights_rag/ -> journey_autopilot/);
# keep the Chroma store under the package data dir, consistent with config.py.
BASE_DIR = Path(__file__).resolve().parents[2]
CHROMA_PATH = Path(os.getenv("CHROMA_PATH", str(BASE_DIR / "data" / "chromadb")))

class FahrgastrechteRAG:

    def __init__(self):
        self.client = chromadb.PersistentClient(path=str(CHROMA_PATH))

        # Multilingual model because of German texts
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-mpnet-base-v2"
        )
        
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

    def retrieve(self, query: str, n_results: int = 3) -> list[str]:
        """
        Finds the most relevant chunks for a query.
        Runs in ~50ms because ChromaDB is local.
        """
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results
        )

        # Return only the texts, not the metadata
        return results["documents"][0]
    
    def retrieve_for_case(
        self,
        delay_minutes: int,
        ticket_type: str,
        bahncard_type: str,
        n_results: int = 3,
    ) -> list[str]:
        query = (
            f"compensation delay {delay_minutes} minutes "
            f"ticket type: {ticket_type} "
            f"BahnCard: {bahncard_type}"
        )
        return self.retrieve(query, n_results=n_results)
