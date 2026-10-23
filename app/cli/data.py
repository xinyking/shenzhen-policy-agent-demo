"""Document collection, local import, search and relevance evaluation only."""
import argparse
import json
from pathlib import Path

from app.evaluation.retrieval import evaluate_retrieval
from app.knowledge.ingestion import collect, load_sources, parse_html, save_snapshot, search_documents, write_json
from app.utils.storage_budget import directory_bytes, MAX_TOTAL_BYTES


def main():
    parser = argparse.ArgumentParser(description='政策原文采集与检索')
    commands = parser.add_subparsers(dest='command', required=True)
    storage = commands.add_parser('storage')
    storage.add_argument('--root', default='.')
    index = commands.add_parser('index')
    index.add_argument('--store', required=True)
    collection = commands.add_parser('collect')
    collection.add_argument('--sources', required=True)
    collection.add_argument('--store', required=True)
    collection.add_argument('--tls-curve', choices=('prime256v1',))
    local = commands.add_parser('import-html')
    local.add_argument('--sources', required=True)
    local.add_argument('--source-id', required=True)
    local.add_argument('--file', required=True)
    local.add_argument('--store', required=True)
    for name in ('search', 'evaluate'):
        command = commands.add_parser(name)
        command.add_argument('--store', required=True)
        command.add_argument('--top-k', type=int, default=5)
        command.add_argument('--output', required=name == 'evaluate')
        command.add_argument('--query' if name == 'search' else '--dataset', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'storage':
            used = directory_bytes(Path(args.root).resolve())
            print(json.dumps({'used_bytes': used, 'limit_bytes': MAX_TOTAL_BYTES,
                'remaining_bytes': max(0, MAX_TOTAL_BYTES - used), 'over_limit': used > MAX_TOTAL_BYTES}, indent=2))
        elif args.command == 'index':
            from app.knowledge.database import build_database
            from app.knowledge.ingestion import current_documents
            print(json.dumps(build_database(args.store, current_documents(args.store)), ensure_ascii=False))
        elif args.command == 'collect':
            results = collect(args.sources, args.store, args.tls_curve)
            print(json.dumps(results, ensure_ascii=False, indent=2))
            if not all(item['ok'] for item in results):
                parser.exit(2, '部分采集失败，成功快照保留。\n')
        elif args.command == 'import-html':
            config = load_sources(args.sources)
            source = next((item for item in config['sources'] if item['id'] == args.source_id), None)
            if source is None:
                raise ValueError('来源不存在')
            raw = Path(args.file).read_bytes()
            if len(raw) > 4_000_000:
                raise ValueError('文件过大')
            print(json.dumps(save_snapshot(args.store, source, raw, parse_html(raw, source), 'local_import'), ensure_ascii=False))
        else:
            if not 1 <= args.top_k <= 50:
                raise ValueError('top-k范围为1–50')
            if args.command == 'search':
                if not args.query.strip():
                    raise ValueError('查询不能为空')
                result = {'query': args.query, 'hits': search_documents(args.store, args.query, args.top_k)}
            else:
                result = evaluate_retrieval(args.store, args.dataset, args.top_k)
            if args.output:
                write_json(args.output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'数据操作失败：{exc}\n')


if __name__ == '__main__':
    main()
