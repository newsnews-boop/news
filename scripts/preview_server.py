"""site/ をローカルで配信する確認用サーバー（毎回最新のファイルを読み込ませる）。"""
import functools
import http.server
import pathlib

SITE = pathlib.Path(__file__).resolve().parent.parent / "site"


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    handler = functools.partial(NoCacheHandler, directory=str(SITE))
    print("http://localhost:8000/ で表示中（終了するにはこの画面を閉じてください）")
    http.server.ThreadingHTTPServer(("localhost", 8000), handler).serve_forever()
