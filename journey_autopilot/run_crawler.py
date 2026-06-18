# run_crawler.py
import os
import sys
from pathlib import Path

# --- PATH SETUP FOR RELATIVE IMPORTS ---
current_file = Path(__file__).resolve()
package_dir = current_file.parent
project_root = current_file.parent.parent

# When this file is executed as `python journey_autopilot/run_crawler.py`,
# Python puts `journey_autopilot/` itself on sys.path. That can shadow stdlib
# modules such as `calendar` with `journey_autopilot/calendar`.
if str(package_dir) in sys.path:
    sys.path.remove(str(package_dir))

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

os.environ["PYTHONPATH"] = str(project_root)

from journey_autopilot.passenger_rights.crawler import crawl_all
from journey_autopilot.passenger_rights.rag_store import FahrgastrechteRAG

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
