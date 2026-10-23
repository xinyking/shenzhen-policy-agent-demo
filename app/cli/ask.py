"""Ask a question against collected official documents."""
import argparse
from pathlib import Path

from app.services.policy_service import PolicyService, ServiceUnavailable, Settings


def main():
    parser = argparse.ArgumentParser(description='深圳政务政策问答')
    parser.add_argument('--store', default='data/store')
    parser.add_argument('--question', required=True)
    parser.add_argument('--context', default='')
    parser.add_argument('--top-k', type=int, default=5)
    parser.add_argument('--mode', choices=('offline', 'llm', 'agent'), default='offline')
    parser.add_argument('--output')
    args = parser.parse_args()
    try:
        result = PolicyService(Settings(Path(args.store))).ask(args.question, args.context, args.top_k, args.mode)
        if args.output:
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(result['report_markdown'], encoding='utf-8')
        print(result['report_markdown'])
    except (ServiceUnavailable, ValueError, OSError, KeyError, TypeError):
        parser.exit(2, '问答失败：请检查参数、采集状态及快照完整性。\n')


if __name__ == '__main__':
    main()
