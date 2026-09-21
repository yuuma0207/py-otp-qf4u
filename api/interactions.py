"""Discord Interactions エンドポイント（Vercel Python サーバーレス関数）。

Vercel の Python ランタイムは `handler(BaseHTTPRequestHandler)` を検出して
各リクエストを処理する。フレームワーク不要・依存は pyotp / pynacl のみ。

Discord の仕様:
  - 全リクエストに Ed25519 署名が付く。検証に失敗したら 401 を返さないと
    Discord は Interactions Endpoint URL を受け付けない。
  - type=1 (PING) には {"type": 1} (PONG) を返す。
  - type=2 (APPLICATION_COMMAND) には {"type": 4, ...} で即時応答する。
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler

# api/_lib.py を同ディレクトリから読み込む。Vercel 上でも同梱される。
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_lib", Path(__file__).resolve().parent / "_lib.py"
)
_lib = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
import sys as _sys

_sys.modules["_lib"] = _lib
_spec.loader.exec_module(_lib)  # type: ignore[union-attr]


# Discord の Interaction / Response 種別。
INTERACTION_PING = 1
INTERACTION_APPLICATION_COMMAND = 2
RESPONSE_PONG = 1
RESPONSE_CHANNEL_MESSAGE = 4  # CHANNEL_MESSAGE_WITH_SOURCE（チャンネルに表示）


class handler(BaseHTTPRequestHandler):
    # Vercel のログを汚さないよう標準のアクセスログは黙らせる。
    def log_message(self, *args, **kwargs):  # noqa: D401
        pass

    def do_GET(self):
        """ヘルスチェック用。"""
        self._send_json(200, {"status": "ok"})

    def do_POST(self):
        length = int(self.headers.get("content-length", 0) or 0)
        body = self.rfile.read(length) if length else b""

        public_key = os.environ.get("DISCORD_PUBLIC_KEY", "")
        signature = self.headers.get("X-Signature-Ed25519", "")
        timestamp = self.headers.get("X-Signature-Timestamp", "")

        # 署名検証（失敗は必ず 401）。
        if not _lib.verify_discord_signature(public_key, signature, timestamp, body):
            self._send_text(401, "invalid request signature")
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._send_text(400, "invalid json")
            return

        interaction_type = payload.get("type")

        # PING → PONG（Discord のエンドポイント検証）。
        if interaction_type == INTERACTION_PING:
            self._send_json(200, {"type": RESPONSE_PONG})
            return

        # スラッシュコマンド。
        if interaction_type == INTERACTION_APPLICATION_COMMAND:
            content = self._build_otp_content()
            self._send_json(
                200,
                {
                    "type": RESPONSE_CHANNEL_MESSAGE,
                    "data": {
                        "content": content,
                        # allowed_mentions を空にして意図しないメンションを防ぐ。
                        "allowed_mentions": {"parse": []},
                    },
                },
            )
            return

        # 未対応の種別。
        self._send_text(400, "unsupported interaction type")

    # --- helpers ---------------------------------------------------------

    def _build_otp_content(self) -> str:
        try:
            results = _lib.generate_otps()
            return _lib.format_otp_message(results)
        except Exception as exc:  # 設定不備などは利用者に見える形で返す。
            return f"⚠️ OTP の生成に失敗しました: {exc}"

    def _send_json(self, status: int, obj: dict):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, status: int, text: str):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
