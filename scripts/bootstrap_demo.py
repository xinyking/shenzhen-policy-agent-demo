"""Build a local catalog from bundled official HTML, without network calls."""
from pathlib import Path
from app.knowledge.ingestion import load_sources, parse_html, save_snapshot, current_documents
from app.knowledge.database import build_database
from app.utils.path_utils import PROJECT_ROOT


def main():
    root = PROJECT_ROOT
    store = root / 'data/store'
    config = load_sources(root / 'data/demo/sources.json')
    for source in config['sources']:
        raw = (root / 'data/demo/articles' / (source['id'] + '.html')).read_bytes()
        save_snapshot(store, source, raw, parse_html(raw, source), acquisition='bundled_snapshot')
    result = build_database(store, current_documents(store))
    print(f"Catalog ready: {result['documents']} documents, {result['paragraphs']} paragraphs")


if __name__ == '__main__':
    main()
