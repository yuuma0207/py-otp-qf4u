"""api/_lib.py の純粋ロジックのテスト。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pyotp
import pytest

# api/_lib.py を "_lib" として直接ロード（api は _ 始まりで通常の import がしにくいため）。
_SPEC = importlib.util.spec_from_file_location(
    "_lib", Path(__file__).resolve().parent.parent / "api" / "_lib.py"
)
lib = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
# dataclass の型解決が sys.modules を参照するため、exec 前に登録しておく。
sys.modules["_lib"] = lib
_SPEC.loader.exec_module(lib)


# --- env_key_for ---------------------------------------------------------

@pytest.mark.parametrize(
    "name,expected",
    [
        ("Google", "TOTP_SECRET_GOOGLE"),
        ("GitHub", "TOTP_SECRET_GITHUB"),
        ("My Service", "TOTP_SECRET_MY_SERVICE"),
        ("aws-prod", "TOTP_SECRET_AWS_PROD"),
        ("  spaced  ", "TOTP_SECRET_SPACED"),
    ],
)
def test_env_key_for(name, expected):
    assert lib.env_key_for(name) == expected


# --- load_accounts -------------------------------------------------------

def _write_config(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_accounts_order_preserved(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
[[accounts]]
name = "Google"
qr = "google.png"

[[accounts]]
name = "GitHub"
qr = "github.png"
""",
    )
    accounts = lib.load_accounts(cfg)
    assert [a.name for a in accounts] == ["Google", "GitHub"]
    assert accounts[0].qr == "google.png"
    assert accounts[0].env_key == "TOTP_SECRET_GOOGLE"


def test_load_accounts_empty_raises(tmp_path):
    cfg = _write_config(tmp_path, "# no accounts\n")
    with pytest.raises(ValueError):
        lib.load_accounts(cfg)


def test_load_accounts_missing_name_raises(tmp_path):
    cfg = _write_config(tmp_path, '[[accounts]]\nqr = "x.png"\n')
    with pytest.raises(ValueError):
        lib.load_accounts(cfg)


def test_load_accounts_env_key_collision_raises(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
[[accounts]]
name = "My Service"
qr = "a.png"

[[accounts]]
name = "My-Service"
qr = "b.png"
""",
    )
    with pytest.raises(ValueError):
        lib.load_accounts(cfg)


# --- generate_otps -------------------------------------------------------

def test_generate_otps_matches_pyotp():
    secret = pyotp.random_base32()
    accounts = [lib.Account(name="Google", qr="g.png")]
    env = {"TOTP_SECRET_GOOGLE": secret}
    results = lib.generate_otps(accounts, env=env)
    assert len(results) == 1
    assert results[0].name == "Google"
    assert results[0].error is None
    assert results[0].code == pyotp.TOTP(secret).now()


def test_generate_otps_missing_secret_is_reported_not_fatal():
    secret = pyotp.random_base32()
    accounts = [
        lib.Account(name="Google", qr="g.png"),
        lib.Account(name="GitHub", qr="h.png"),
    ]
    env = {"TOTP_SECRET_GOOGLE": secret}  # GitHub 未設定
    results = lib.generate_otps(accounts, env=env)
    assert results[0].code is not None
    assert results[1].code is None
    assert "TOTP_SECRET_GITHUB" in results[1].error


def test_generate_otps_invalid_secret_reported():
    accounts = [lib.Account(name="Bad", qr="b.png")]
    env = {"TOTP_SECRET_BAD": "not valid base32 !!!"}
    results = lib.generate_otps(accounts, env=env)
    assert results[0].code is None
    assert results[0].error


# --- format_otp_message --------------------------------------------------

def test_format_otp_message_contains_codes():
    results = [
        lib.OtpResult(name="Google", code="123456"),
        lib.OtpResult(name="GitHub", code=None, error="未設定 (TOTP_SECRET_GITHUB)"),
    ]
    msg = lib.format_otp_message(results)
    assert "123456" in msg
    assert "Google" in msg
    assert "GitHub" in msg
    assert "⚠️" in msg


# --- verify_discord_signature -------------------------------------------

def test_verify_discord_signature_roundtrip():
    from nacl.signing import SigningKey

    sk = SigningKey.generate()
    public_key = sk.verify_key.encode().hex()

    timestamp = "1700000000"
    body = b'{"type":1}'
    signature = sk.sign(timestamp.encode() + body).signature.hex()

    assert lib.verify_discord_signature(public_key, signature, timestamp, body) is True
    # 改ざんされた body は失敗する
    assert lib.verify_discord_signature(public_key, signature, timestamp, b"tampered") is False
    # 不正な署名は失敗する
    assert lib.verify_discord_signature(public_key, "00" * 64, timestamp, body) is False
    # 空ヘッダは失敗する
    assert lib.verify_discord_signature(public_key, "", timestamp, body) is False
