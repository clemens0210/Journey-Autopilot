import chromadb
from chromadb.utils import embedding_functions
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parents[1]
CHROMA_PATH = Path(os.getenv("CHROMA_PATH", str(BASE_DIR / "data" / "chromadb")))

class FahrgastrechteRAG:
    
    def __init__(self):
        self.client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        
        # Multilinguales Modell wegen deutschen Texten
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-mpnet-base-v2"
        )
        
        self.collection = self.client.get_or_create_collection(
            name="fahrgastrechte",
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )
    
    # -------------------------
    # INDEXIERUNG (täglich)
    # -------------------------
    
    def rebuild_index(self, documents: list[dict]):
        """Alten Index löschen und neu aufbauen"""
        
        # Alte Daten löschen damit nichts veraltet bleibt
        try:
            self.client.delete_collection("fahrgastrechte")
        except:
            pass
        
        self.collection = self.client.get_or_create_collection(
            name="fahrgastrechte",
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )
        
        # Dokumente chunken und speichern
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
        
        # In ChromaDB speichern (berechnet Embeddings automatisch)
        self.collection.add(
            documents=all_chunks,
            ids=all_ids,
            metadatas=all_metadata
        )
        
        print(f"Index built: {len(all_chunks)} chunks from {len(documents)} pages")
    
    def _chunk_text(self, text: str, chunk_size: int = 400) -> list[str]:
        """Text an Absatzgrenzen aufteilen, nicht mitten im Satz"""
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
    # QUERY (bei jeder Verspätung)
    # -------------------------
    
    def retrieve(self, query: str, n_results: int = 3) -> list[str]:
        """
        Findet die relevantesten Chunks für eine Anfrage.
        Läuft in ~50ms weil ChromaDB lokal ist.
        """
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results
        )
        
        # Nur die Texte zurückgeben, nicht die Metadaten
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
