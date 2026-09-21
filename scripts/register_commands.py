"""Discord に /otp スラッシュコマンドを登録するスクリプト。

環境変数（.env または mise 経由）:
  DISCORD_APPLICATION_ID  必須
  DISCORD_BOT_TOKEN       必須
  DISCORD_GUILD_ID        任意。指定するとそのサーバーに即時登録（テスト向け）。
                          未指定ならグローバル登録（全サーバー・反映に最大1時間）。

使い方:
  uv run register
"""

from __future__ import annotations

import os
import sys

import requests

API_BASE = "https://discord.com/api/v10"

COMMAND = {
    "name": "otp",
    "description": "共有アカウントの現在のワンタイムパスコードを表示します",
    "type": 1,  # CHAT_INPUT
}


def main() -> int:
    app_id = os.environ.get("DISCORD_APPLICATION_ID")
    bot_token = os.environ.get("DISCORD_BOT_TOKEN")
    guild_id = os.environ.get("DISCORD_GUILD_ID")

    missing = [
        name
        for name, val in [
            ("DISCORD_APPLICATION_ID", app_id),
            ("DISCORD_BOT_TOKEN", bot_token),
        ]
        if not val
    ]
    if missing:
        print(f"環境変数が未設定です: {', '.join(missing)}", file=sys.stderr)
        print("secrets/.env か .env に設定してください（.env.example 参照）。", file=sys.stderr)
        return 1

    if guild_id:
        url = f"{API_BASE}/applications/{app_id}/guilds/{guild_id}/commands"
        scope = f"ギルド {guild_id}（即時反映）"
    else:
        url = f"{API_BASE}/applications/{app_id}/commands"
        scope = "グローバル（反映に最大1時間）"

    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    print(f"/otp を登録します -> {scope}")
    resp = requests.post(url, headers=headers, json=COMMAND, timeout=10)

    if resp.status_code in (200, 201):
        data = resp.json()
        print(f"✓ 登録成功: /{data.get('name')} (id={data.get('id')})")
        return 0

    print(f"✗ 登録失敗: HTTP {resp.status_code}", file=sys.stderr)
    print(resp.text, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
