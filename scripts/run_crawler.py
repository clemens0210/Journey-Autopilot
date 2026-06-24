# run_crawler.py
import os
import sys
from pathlib import Path

# --- PATH SETUP (src/ layout) ---
# Make the src/ layout importable when run directly without an editable install.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
os.environ["PYTHONPATH"] = str(_SRC)

from journey_autopilot.integrations.rights_rag.crawler import crawl_all
from journey_autopilot.integrations.rights_rag.rag_store import FahrgastrechteRAG

def main():
    print("Starting live crawler for DB passenger rights...")
    
    documents = crawl_all()
    if not documents:
        print("No documents crawled. Aborting.")
        return
        
    print(f"\n{len(documents)} pages successfully cleaned in memory.")
    print("Initialising vector database and creating embeddings...")
    
    try:
        rag = FahrgastrechteRAG()
        rag.rebuild_index(documents) 
        print("ChromaDB successfully updated with live data!")
    except Exception as e:
        print(f"Error saving to ChromaDB: {e}")

if __name__ == "__main__":
    main()
