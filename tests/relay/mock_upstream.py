"""Fake DeepSeek, Tavily, Serper and Hunter for testing the relay locally (no keys, no cost).
Every chat answer reports 1,000 input and 500 output tokens."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def _send(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if self.path == "/chat/completions":
            assert self.headers.get("authorization") == "Bearer real-deepseek-key", "relay must send the real key"
            return self._send({"choices": [{"message": {"role": "assistant", "content": "{\"ok\": true}"}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1000},
                               "model": req.get("model")})
        if self.path == "/search" and self.headers.get("authorization"):
            return self._send({"answer": "", "results": [{"title": "T", "url": "https://jobs.lever.co/acme/1", "content": "c"}], "depth": req.get("search_depth")})
        if self.path == "/search":
            return self._send({"organic": [{"title": "G", "link": "https://jobs.ashbyhq.com/acme", "snippet": "s"}]})
        self._send({"error": "unknown"}, 404)

    def do_GET(self):
        if self.path.startswith("/v2/email-finder"):
            return self._send({"data": {"email": "jane@acme.com", "score": 91}})
        self._send({"error": "unknown"}, 404)

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 9911), H).serve_forever()
