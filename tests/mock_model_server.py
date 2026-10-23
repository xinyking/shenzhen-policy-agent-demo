"""Loopback-only scripted provider for UI integration; not a real LLM."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path != '/v1/chat/completions':
            self.send_error(404)
            return
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        instruction = request['messages'][0]['content']
        payload = json.loads(request['messages'][1]['content'])
        if '连接测试' in instruction:
            result = {'ok': True}
        elif '政策研究Agent' in instruction:
            result = {'action': 'answer', 'arguments': {}, 'gaps': []}
        elif '证据评审Agent' in instruction:
            result = {'verdicts': [{'claim_index': i, 'supported': True, 'reason': '固定脚本模拟，非真实核验'} for i in range(len(payload['claims']))]}
        else:
            evidence = payload['evidence'][0]
            result = {'claims': [{'text': '【接口联调模拟】' + evidence['text'][:100], 'evidence_ids': [evidence['evidence_id']]}], 'missing_information': ['这是固定脚本模拟，不代表真实模型效果。']}
        raw = json.dumps({'choices': [{'message': {'content': json.dumps(result, ensure_ascii=False)}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='仅供联调的固定脚本模型，非真实LLM')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    ThreadingHTTPServer(('127.0.0.1', args.port), Provider).serve_forever()
