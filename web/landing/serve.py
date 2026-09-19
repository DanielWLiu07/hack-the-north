#!/usr/bin/env python3
# dev server: like `python3 -m http.server 8123` but sends no-store so
# Chrome never serves stale module JS (no cmd+shift+r needed)
import http.server

class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, must-revalidate')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_POST(self):
        # tiny debug sink: the page can POST /log lines during repro runs
        if self.path == '/log':
            n = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(n).decode('utf-8', 'replace')
            with open('devlog.txt', 'a') as f:
                f.write(body + '\n')
            self.send_response(204)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == '__main__':
    http.server.test(HandlerClass=NoCacheHandler, port=8124, bind='127.0.0.1')
