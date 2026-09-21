"""OTP 生成・設定読み込み・Discord 署名検証の共通ロジック。

`api/interactions.py`（Vercel 関数）と `scripts/`（ローカル）の両方から使う。
ファイル名を `_` 始まりにしているのは、Vercel が api/ 配下をルートとして
公開する際に `_` 始まりのファイルを無視するため（＝これはエンドポイントにならない）。

実行時依存は pyotp / pynacl のみ。QR デコード等のローカル専用依存はここに持ち込まない。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pyotp

# Python 3.11+ 標準の tomllib を使う。
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Vercel/ローカルとも 3.12 前提
    import tomli as tomllib  # type: ignore

# リポジトリルート（このファイルは <root>/api/_lib.py）。
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"

ENV_KEY_PREFIX = "TOTP_SECRET_"


@dataclass(frozen=True)
class Account:
    """config.toml の 1 アカウント分。"""

    name: str  # 表示名（例: "Google"）
    qr: str  # secrets/ 配下の QR 画像ファイル名（sync でのみ使用）

    @property
    def env_key(self) -> str:
        """このアカウントのシークレットが入る環境変数名。"""
        return env_key_for(self.name)


def env_key_for(name: str) -> str:
    """アカウント名から環境変数キーを導出する。

    大文字化し、英数字以外を _ に置換し、前後の _ を除去する。
        "Google"     -> "TOTP_SECRET_GOOGLE"
        "GitHub"     -> "TOTP_SECRET_GITHUB"
        "My Service" -> "TOTP_SECRET_MY_SERVICE"
    """
    slug = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    return ENV_KEY_PREFIX + slug


def load_accounts(config_path: Path | str | None = None) -> list[Account]:
    """config.toml を読み、Account のリストを設定順に返す。

    config_path 省略時は環境変数 OTP_CONFIG_PATH、無ければ既定の CONFIG_PATH。
    """
    if config_path is None:
        config_path = os.environ.get("OTP_CONFIG_PATH", CONFIG_PATH)
    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    accounts: list[Account] = []
    for entry in data.get("accounts", []):
        name = entry.get("name")
        qr = entry.get("qr", "")
        if not name:
            raise ValueError(f"config.toml のアカウントに name がありません: {entry!r}")
        accounts.append(Account(name=name, qr=qr))

    if not accounts:
        raise ValueError("config.toml に [[accounts]] が 1 つもありません")

    # env_key の衝突（例: "GitHub" と "Git Hub" が同じキーになる）を検出。
    seen: dict[str, str] = {}
    for acc in accounts:
        if acc.env_key in seen:
            raise ValueError(
                f"アカウント名 {acc.name!r} と {seen[acc.env_key]!r} が同じ環境変数キー "
                f"{acc.env_key} に解決されます。名前を変えてください。"
            )
        seen[acc.env_key] = acc.name
    return accounts


@dataclass(frozen=True)
class OtpResult:
    name: str
    code: str | None  # 生成できた OTP。シークレット未設定なら None。
    error: str | None = None


def generate_otps(
    accounts: list[Account] | None = None,
    env: dict[str, str] | None = None,
) -> list[OtpResult]:
    """全アカウントの現在の OTP を設定順に生成する。

    シークレットが環境変数に無いアカウントは code=None + error を返し、
    他のアカウントの生成は止めない（部分的に設定されていても動くように）。
    """
    if accounts is None:
        accounts = load_accounts()
    if env is None:
        env = dict(os.environ)

    results: list[OtpResult] = []
    for acc in accounts:
        secret = env.get(acc.env_key)
        if not secret:
            results.append(
                OtpResult(name=acc.name, code=None, error=f"未設定 ({acc.env_key})")
            )
            continue
        try:
            code = pyotp.TOTP(secret).now()
            results.append(OtpResult(name=acc.name, code=code))
        except Exception as exc:  # 不正な Base32 など
            results.append(OtpResult(name=acc.name, code=None, error=str(exc)))
    return results


def format_otp_message(results: list[OtpResult]) -> str:
    """OTP 結果を Discord に表示するメッセージへ整形する。"""
    lines = ["🔐 **ワンタイムパスコード**"]
    width = max((len(r.name) for r in results), default=0)
    for r in results:
        label = f"{r.name}:".ljust(width + 1)
        if r.code is not None:
            lines.append(f"`{label} {r.code}`")
        else:
            lines.append(f"`{label}` ⚠️ {r.error}")
    lines.append("_30秒ごとに更新されます_")
    return "\n".join(lines)


def verify_discord_signature(
    public_key: str, signature: str, timestamp: str, body: bytes
) -> bool:
    """Discord の Ed25519 署名を検証する。

    Discord は各リクエストに X-Signature-Ed25519 / X-Signature-Timestamp を付ける。
    検証対象は (timestamp + raw_body)。失敗時は False を返す（例外を投げない）。
    """
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    if not (public_key and signature and timestamp):
        return False
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key))
        verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False
