"""api/interactions.py をローカル HTTP サーバとして起動し、Discord からの
リクエストを模して署名検証・PING・コマンド応答を end-to-end に検証する。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pyotp
import pytest
import requests
from nacl.signing import SigningKey

ROOT = Path(__file__).resolve().parent.parent


def _load_handler():
    spec = importlib.util.spec_from_file_location(
        "interactions", ROOT / "api" / "interactions.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["interactions"] = mod
    spec.loader.exec_module(mod)
    return mod.handler


@pytest.fixture()
def server(tmp_path):
    """署名鍵と TOTP シークレットを設定し、ハンドラをローカルで起動する。

    実際の config.toml に依存しないよう、テスト用 config を OTP_CONFIG_PATH で差し込む。
    """
    signing_key = SigningKey.generate()
    public_key = signing_key.verify_key.encode().hex()

    google_secret = pyotp.random_base32()
    github_secret = pyotp.random_base32()

    test_config = tmp_path / "config.toml"
    test_config.write_text(
        '[[accounts]]\nname = "Google"\nqr = "g.png"\n\n'
        '[[accounts]]\nname = "GitHub"\nqr = "h.png"\n',
        encoding="utf-8",
    )

    os.environ["OTP_CONFIG_PATH"] = str(test_config)
    os.environ["DISCORD_PUBLIC_KEY"] = public_key
    os.environ["TOTP_SECRET_GOOGLE"] = google_secret
    os.environ["TOTP_SECRET_GITHUB"] = github_secret

    handler = _load_handler()
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    yield {
        "url": f"http://127.0.0.1:{port}/api/interactions",
        "signing_key": signing_key,
        "google_secret": google_secret,
        "github_secret": github_secret,
    }

    httpd.shutdown()
    httpd.server_close()
    os.environ.pop("OTP_CONFIG_PATH", None)


def _post(url, signing_key, payload, *, sign=True, timestamp="1700000000"):
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if sign:
        sig = signing_key.sign(timestamp.encode() + body).signature.hex()
        headers["X-Signature-Ed25519"] = sig
        headers["X-Signature-Timestamp"] = timestamp
    return requests.post(url, data=body, headers=headers, timeout=5)


def test_ping_returns_pong(server):
    resp = _post(server["url"], server["signing_key"], {"type": 1})
    assert resp.status_code == 200
    assert resp.json() == {"type": 1}


def test_command_returns_current_otps(server):
    resp = _post(
        server["url"],
        server["signing_key"],
        {"type": 2, "data": {"name": "otp"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == 4
    content = data["data"]["content"]
    # 実際の現在コードが含まれること。
    assert pyotp.TOTP(server["google_secret"]).now() in content
    assert pyotp.TOTP(server["github_secret"]).now() in content


def test_bad_signature_is_rejected(server):
    resp = _post(
        server["url"],
        server["signing_key"],
        {"type": 1},
        sign=False,
    )
    assert resp.status_code == 401


def test_tampered_body_is_rejected(server):
    # 正しい署名を作った後に body を差し替える。
    signing_key = server["signing_key"]
    timestamp = "1700000000"
    good_body = json.dumps({"type": 1}).encode()
    sig = signing_key.sign(timestamp.encode() + good_body).signature.hex()
    resp = requests.post(
        server["url"],
        data=b'{"type":2}',  # 署名と一致しない
        headers={
            "X-Signature-Ed25519": sig,
            "X-Signature-Timestamp": timestamp,
        },
        timeout=5,
    )
    assert resp.status_code == 401


def test_health_check_get(server):
    resp = requests.get(server["url"], timeout=5)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
