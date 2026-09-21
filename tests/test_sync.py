"""scripts/sync.py の QR デコード〜シークレット抽出を end-to-end に検証する。

qrcode で本物の otpauth QR 画像を生成し、cv2 でデコードして元の
シークレットに戻ることを確認する（この環境で QR 読み取りが動く保証）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pyotp
import pytest
import qrcode

ROOT = Path(__file__).resolve().parent.parent


def _load_sync():
    spec = importlib.util.spec_from_file_location("sync", ROOT / "scripts" / "sync.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sync"] = mod
    spec.loader.exec_module(mod)
    return mod


sync = _load_sync()


def _make_qr(uri: str, path: Path) -> None:
    img = qrcode.make(uri)
    img.save(str(path))


def test_decode_qr_roundtrip(tmp_path):
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name="dev@example.com", issuer_name="Google")
    qr_path = tmp_path / "google.png"
    _make_qr(uri, qr_path)

    decoded = sync.decode_qr(qr_path)
    assert decoded == uri
    assert sync.secret_from_otpauth(decoded, "Google") == secret


def test_decode_qr_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        sync.decode_qr(tmp_path / "nope.png")


def test_secret_from_otpauth_rejects_migration_format():
    with pytest.raises(ValueError, match="migration"):
        sync.secret_from_otpauth("otpauth-migration://offline?data=AAAA", "Google")


def test_secret_from_otpauth_rejects_non_otpauth():
    with pytest.raises(ValueError):
        sync.secret_from_otpauth("https://example.com", "Google")


def test_merge_env_preserves_other_lines(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "DISCORD_PUBLIC_KEY=abc123\n"
        "DISCORD_BOT_TOKEN=tok\n"
        "TOTP_SECRET_GOOGLE=OLDVALUE\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sync, "ENV_OUT", env_file)

    sync.merge_env([("TOTP_SECRET_GOOGLE", "NEWVALUE"), ("TOTP_SECRET_GITHUB", "GH")])

    text = env_file.read_text(encoding="utf-8")
    assert "# comment" in text
    assert "DISCORD_PUBLIC_KEY=abc123" in text  # 他の行は保持
    assert "DISCORD_BOT_TOKEN=tok" in text
    assert "TOTP_SECRET_GOOGLE=NEWVALUE" in text  # 既存キーは更新
    assert "TOTP_SECRET_GOOGLE=OLDVALUE" not in text
    assert "TOTP_SECRET_GITHUB=GH" in text  # 新規キーは追記


def test_merge_env_creates_file_when_absent(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(sync, "ENV_OUT", env_file)
    sync.merge_env([("TOTP_SECRET_GOOGLE", "V")])
    assert env_file.read_text(encoding="utf-8").strip() == "TOTP_SECRET_GOOGLE=V"
