"""Временная страничка для проверки: видит ли телефон компьютер по сети."""
import http.server
import socket
import socketserver

PORT = 8765
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Связь есть</title></head>
<body style="font-family:system-ui;text-align:center;padding:15vh 6vw;background:#0b7">
<div style="font-size:22vw">&#10003;</div>
<h1 style="color:#fff;font-size:8vw">Связь есть!</h1>
<p style="color:#fff;font-size:5vw">Телефон видит компьютер.<br>Веб-интерфейс возможен.</p>
</body></html>""".encode("utf-8")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)
        print(f"ОТКРЫЛОСЬ! Заходил: {self.client_address[0]}", flush=True)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ip = socket.gethostbyname(socket.gethostname())
    print(f"Тестовая страница: http://{ip}:{PORT}", flush=True)
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as srv:
        srv.serve_forever()
