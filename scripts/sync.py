"""QR 画像からシークレットを抽出し、Vercel 用の環境変数を準備するスクリプト。

やること:
  1. config.toml を読む
  2. 各アカウントの QR 画像 (secrets/<qr>) をデコードして otpauth URI を得る
  3. URI から Base32 シークレットを取り出す
  4. secrets/.env に TOTP_SECRET_<NAME>=... を書き出す（ローカル実行用）
  5. --push 指定時は Vercel のプロジェクト環境変数へ登録する

QR 画像 = 2FA シークレットそのもの。secrets/ は .gitignore 済み。

使い方:
  uv run sync                # secrets/.env を生成
  uv run sync --push         # 加えて Vercel(production) に登録（要 `vercel link`）
  uv run sync --push --env preview production development
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import cv2  # opencv-python-headless（ローカル専用依存）
import pyotp

ROOT = Path(__file__).resolve().parent.parent
SECRETS_DIR = ROOT / "secrets"
ENV_OUT = SECRETS_DIR / ".env"

# api/_lib.py を読み込む（Account / env_key / load_accounts を再利用）。
_spec = importlib.util.spec_from_file_location("_lib", ROOT / "api" / "_lib.py")
_lib = importlib.util.module_from_spec(_spec)
sys.modules["_lib"] = _lib
_spec.loader.exec_module(_lib)


def decode_qr(image_path: Path) -> str:
    """QR 画像をデコードして中身の文字列（otpauth URI）を返す。"""
    if not image_path.exists():
        raise FileNotFoundError(f"QR 画像が見つかりません: {image_path}")

    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"画像を読み込めませんでした（形式を確認）: {image_path}")

    detector = cv2.QRCodeDetector()
    data, points, _ = detector.detectAndDecode(img)
    if not data:
        raise ValueError(
            f"QR コードを検出できませんでした: {image_path}\n"
            "画像がぼやけている/トリミングが必要な可能性があります。"
        )
    return data


def secret_from_otpauth(uri: str, account_name: str) -> str:
    """otpauth URI から Base32 シークレットを取り出す。"""
    if uri.startswith("otpauth-migration://"):
        raise ValueError(
            f"[{account_name}] は Google Authenticator のエクスポート形式 "
            "(otpauth-migration://) です。個別サービスの登録用 QR を使ってください。"
        )
    if not uri.startswith("otpauth://"):
        raise ValueError(
            f"[{account_name}] QR の中身が otpauth URI ではありません: {uri[:40]}..."
        )
    totp = pyotp.parse_uri(uri)  # 不正なら例外
    return totp.secret


def main() -> int:
    parser = argparse.ArgumentParser(description="QR からシークレットを同期する")
    parser.add_argument(
        "--push", action="store_true", help="Vercel の環境変数にも登録する"
    )
    parser.add_argument(
        "--env",
        nargs="+",
        default=["production"],
        help="--push の対象環境（既定: production）",
    )
    args = parser.parse_args()

    accounts = _lib.load_accounts()
    SECRETS_DIR.mkdir(exist_ok=True)

    resolved: list[tuple[str, str]] = []  # (env_key, secret)
    errors: list[str] = []

    for acc in accounts:
        qr_path = SECRETS_DIR / acc.qr
        try:
            uri = decode_qr(qr_path)
            secret = secret_from_otpauth(uri, acc.name)
        except Exception as exc:
            errors.append(f"✗ {acc.name}: {exc}")
            continue
        resolved.append((acc.env_key, secret))
        # シークレットはコンソールに出さず、検出できたことだけ示す。
        print(f"✓ {acc.name:<15} -> {acc.env_key} (現在: {pyotp.TOTP(secret).now()})")

    for e in errors:
        print(e, file=sys.stderr)

    if not resolved:
        print("\n登録できたアカウントがありません。QR 画像と config.toml を確認してください。", file=sys.stderr)
        return 1

    # secrets/.env にマージ書き込み（DISCORD_* など他の行は保持する）。
    merge_env(resolved)
    print(f"\n{ENV_OUT} に {len(resolved)} 件を反映しました（他の行は保持・ローカル実行用）。")

    if args.push:
        push_to_vercel(resolved, args.env)

    if errors:
        print(f"\n⚠️ {len(errors)} 件のアカウントは失敗しました（上記）。", file=sys.stderr)
        return 1
    return 0


def merge_env(resolved: list[tuple[str, str]]) -> None:
    """secrets/.env に TOTP_SECRET_* を反映する。

    既存ファイルの他の行（DISCORD_* やコメント）はそのまま残し、
    管理対象キー（今回解決できた TOTP_SECRET_*）だけを更新/追記する。
    """
    managed = dict(resolved)
    out_lines: list[str] = []
    seen: set[str] = set()

    if ENV_OUT.exists():
        for line in ENV_OUT.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                key = stripped.split("=", 1)[0].strip()
                if key in managed:
                    out_lines.append(f"{key}={managed[key]}")
                    seen.add(key)
                    continue
            out_lines.append(line)  # コメント・空行・他キーは保持

    # ファイルに無かった管理キーを末尾に追記。
    for key, secret in resolved:
        if key not in seen:
            out_lines.append(f"{key}={secret}")

    ENV_OUT.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def push_to_vercel(resolved: list[tuple[str, str]], environments: list[str]) -> None:
    """Vercel のプロジェクト環境変数へ登録する（要 `vercel link` 済み）。"""
    print(f"\nVercel に登録します（環境: {', '.join(environments)}）...")
    for key, secret in resolved:
        for env in environments:
            # 既存があれば消してから追加（add は重複でエラーになるため）。
            subprocess.run(
                ["vercel", "env", "rm", key, env, "-y"],
                capture_output=True,
                text=True,
            )
            proc = subprocess.run(
                ["vercel", "env", "add", key, env],
                input=secret,
                capture_output=True,
                text=True,
            )
            status = "ok" if proc.returncode == 0 else f"失敗: {proc.stderr.strip()}"
            print(f"  {key} [{env}] -> {status}")
    print("完了。`vercel deploy --prod` で反映してください。")


if __name__ == "__main__":
    raise SystemExit(main())
