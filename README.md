# py-otp-qf4u

Discord のスラッシュコマンド `/otp` で、共有アカウントの TOTP（2段階認証のワンタイムパスコード）を発行する Vercel サーバーレス関数です。

以前 Slack + Vercel で作った [devaccount-qr-otp](https://github.com/yuuma0207/devaccount-qr-otp) の Discord 版。認証アプリを1人しか持てない問題を、コマンド化してチームの誰でもコードを取得できるようにするためのものです。

## 仕組み

```
Discord (/otp) ──► Vercel の HTTP Interactions エンドポイント (api/interactions.py)
                     ├─ Ed25519 署名を検証
                     ├─ config.toml のアカウント一覧を読む
                     ├─ 環境変数 TOTP_SECRET_* から pyotp で OTP 生成
                     └─ チャンネルに現在のコードを返信
```

- **シークレットは repo に入れない。** QR 画像（= シークレットそのもの）はローカルの `secrets/`（gitignore 済み）に置き、`uv run scripts/sync.py` で抽出して Vercel の環境変数に登録します。
- **アカウントは `config.toml` で制御。** 追加は「QR 画像を置く → 1行足す → sync」の3手順。

## アカウントの追加方法

1. `secrets/` に QR 画像を置く（例: `secrets/google.png`）
2. `config.toml` に追記:
   ```toml
   [[accounts]]
   name = "Google"      # 表示名 兼 環境変数キーの元
   qr = "google.png"    # secrets/ 配下のファイル名
   ```
   `name` から環境変数キーが決まります: `Google` → `TOTP_SECRET_GOOGLE`、`My Service` → `TOTP_SECRET_MY_SERVICE`
3. `uv run scripts/sync.py --push` でシークレットを抽出し Vercel に登録

---

## セットアップ

### 1. 環境構築（mise + uv）

```bash
mise trust      # .mise.toml を信頼（初回のみ）
mise install    # Python 3.12 と uv を用意
uv sync         # 依存をインストール（実行時 + ローカル用の QR デコード等）
```

### 2. Discord アプリを作成

[Discord Developer Portal](https://discord.com/developers/applications) で **New Application**。

- **General Information** > `APPLICATION ID` と `PUBLIC KEY` を控える
- **Bot** > `Reset Token` で Bot Token を取得

`.env.example` をコピーして `secrets/.env`（または `.env`）に値を入れる:

```bash
cp .env.example secrets/.env
```

| 変数 | 用途 | どこで使う |
|---|---|---|
| `DISCORD_PUBLIC_KEY` | 署名検証 | Vercel |
| `DISCORD_APPLICATION_ID` | コマンド登録 | ローカル |
| `DISCORD_BOT_TOKEN` | コマンド登録 | ローカル |
| `DISCORD_GUILD_ID` | 任意。テスト用の即時登録先サーバー | ローカル |
| `TOTP_SECRET_*` | OTP のシード | `sync` が自動生成 |

> `secrets/.env` は mise が自動で読み込みます（`.mise.toml` の設定）。

### 3. QR 画像を置いて sync

```bash
# secrets/google.png, secrets/github.png を配置してから
uv run scripts/sync.py          # secrets/.env にシークレットを書き出す
```

### 4. スラッシュコマンドを登録

```bash
uv run scripts/register_commands.py
```

`DISCORD_GUILD_ID` を設定しておくと、そのサーバーに**即時**登録されます（グローバル登録は反映に最大1時間）。テスト中はギルド登録を推奨。

### 5. Vercel にデプロイ

```bash
vercel link                                 # プロジェクトを紐付け（初回のみ）
uv run scripts/sync.py --push               # TOTP_SECRET_* を Vercel に登録
vercel env add DISCORD_PUBLIC_KEY production # Public Key も登録
vercel deploy --prod
```

デプロイ後の URL（例 `https://<app>.vercel.app/api/interactions`）を、Developer Portal の **General Information > Interactions Endpoint URL** に設定します。保存時に Discord が PING を送って検証し、成功すれば緑になります。

### 6. 使う

Discord で `/otp` を実行すると、`config.toml` の全アカウントの現在コードが並んで表示されます:

```
🔐 ワンタイムパスコード
Google: 123456
GitHub: 654321
30秒ごとに更新されます
```

---

## 開発

```bash
uv run pytest          # テスト（署名検証・OTP生成・QRデコード・エンドポイントの e2e）
```

### ファイル構成

| パス | 役割 |
|---|---|
| `api/interactions.py` | Vercel 関数。Discord Interactions を処理（署名検証 / PING / `/otp`） |
| `api/_lib.py` | 共通ロジック（config 読み込み・OTP 生成・署名検証）。`_` 始まりなので Vercel のルートにならない |
| `config.toml` | アカウント一覧（コミットする。秘密情報なし） |
| `scripts/sync.py` | QR → シークレット抽出 → `.env` / Vercel env |
| `scripts/register_commands.py` | `/otp` コマンドの登録 |
| `secrets/` | QR 画像と `.env`（**gitignore 済み・コミット禁止**） |
| `vercel.json` | ルーティングと `config.toml` の同梱設定 |
| `requirements.txt` | Vercel 用の実行時依存（`pyotp`, `pynacl`） |

## セキュリティ上の注意

- **QR 画像 = 2FA シークレットそのもの。** 絶対にコミットしない（`secrets/` は gitignore 済み）。
- `/otp` の応答はチャンネル全員に見えます。OTP を扱うチャンネルは信頼できるメンバーだけがアクセスできるようにしてください。
- Bot Token / Public Key が漏れた場合は Developer Portal で再生成してください。
