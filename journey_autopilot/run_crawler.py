# run_crawler.py
import os
import sys
from pathlib import Path

# --- PATH SETUP FOR RELATIVE IMPORTS ---
current_file = Path(__file__).resolve()
project_root = current_file.parent.parent

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

os.environ["PYTHONPATH"] = str(project_root)

from journey_autopilot.passenger_rights.crawler import crawl_all
from journey_autopilot.passenger_rights.rag_store import FahrgastrechteRAG

def main():
    print("🚀 Starting live crawler for DB passenger rights...")
    
    documents = crawl_all()
    if not documents:
        print("✗ No documents crawled. Aborting.")
        return
        
    print(f"\n📥 {len(documents)} pages successfully cleaned in memory.")
    print("🧠 Initialising vector database and creating embeddings...")
    
    try:
        rag = FahrgastrechteRAG()
        rag.rebuild_index(documents) 
        print("✔ ChromaDB successfully updated with live data!")
    except Exception as e:
        print(f"✗ Error saving to ChromaDB: {e}")

if __name__ == "__main__":
    main()