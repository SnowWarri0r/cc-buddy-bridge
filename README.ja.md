# cc-buddy-bridge

[English](README.md) | [简体中文](README.zh-CN.md) | **日本語**

[![test](https://github.com/SnowWarri0r/cc-buddy-bridge/actions/workflows/test.yml/badge.svg)](https://github.com/SnowWarri0r/cc-buddy-bridge/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](#動作環境)
[![Status: daily-driven](https://img.shields.io/badge/status-daily--driven-brightgreen.svg)](#ステータス)
[![PRs: Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/SnowWarri0r/cc-buddy-bridge/issues)

[Claude Code](https://claude.com/claude-code) CLI のセッションを
[claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy)
の BLE ハードウェアにブリッジします。Claude デスクトップアプリは不要です。

buddy ファームウェアは公式には Claude for macOS/Windows のデスクトップ版とのみペアリングします。
本プロジェクトを使うと、ターミナルで `claude` CLI を起動するだけで同じハードウェアを駆動でき、
デスクペットが CLI セッションに反応します。アイドル時には眠り、ツール呼び出し中は忙しそうにし、
権限プロンプトが必要なときは点滅、そして stick の物理ボタンから直接 allow / deny できます。

## 主な機能

- **重要な操作の物理 2FA** —— `defaultMode: bypassPermissions` を全体に設定しつつ、本当に気をつけたい数個のツールだけを `permissions.ask` に並べます。それらの allow/deny はデスクの buddy にある A/B ボタンで決まります。
- **スマートマッチャー** —— 害のない Bash（`ls`/`cat`/`grep`/...）は自動許可、危険な Bash（`rm`/`curl`/`git push`/...）は常に確認、それ以外は stick に判断を委ねます。デフォルトルールは TOML で上書き可能。
- **リアルタイム stick HUD** —— アシスタントの返信は JSONL tailer 経由で ~500 ms 以内に stick にミラーされます（Stop フックの flush レースを回避）。
- **ステータスライン** —— `cc-buddy-bridge hud` がプロンプトバーにバッテリー / 暗号化状態 / **当日のトークン数** / **当日の USD 推定コスト** / 保留中の権限プロンプトを表示します。[claude-hud](https://github.com/jarrodwatts/claude-hud) と並べて使うことも可能。
- **ワンコマンドのインストール + 自動起動** —— `cc-buddy-bridge install --service` が OS ごとに正しいバックエンドを選びます（macOS は launchd、Linux は systemd ユーザーユニット、Windows はタスクスケジューラ）。
- **カスタム GIF キャラクター** —— `cc-buddy-bridge push-character ./pack/` でフレームの入ったフォルダを BLE 経由でアップロードします。チャンク化されたフロー制御つき。
- **新バージョン通知 + 自動更新** —— デーモンが GitHub releases を 1 日 1 回バックグラウンドで取得し、新タグがあれば hud に `↑ vX.Y.Z` を表示。`cc-buddy-bridge check-update` で明示チェック、`cc-buddy-bridge update` で pull + 再インストール + デーモン再起動まで一気に実行。ポーリング無効化は `CC_BUDDY_BRIDGE_NO_UPDATE_CHECK=1`。
- **stick での CJK 表示（オプション）** —— フォーク専用ファームウェア [SnowWarri0r/claude-desktop-buddy](https://github.com/SnowWarri0r/claude-desktop-buddy) が ASCII 限定の標準フォントを [Fusion Pixel Font](https://github.com/TakWolf/fusion-pixel-font)（OFL）12×12 グリフに置き換えて簡体中国語・日本語を描画（繁体中国語は計画中）。`CC_BUDDY_CJK_TARGET=zh-CN`（または `ja`）を設定するとブリッジが自動で対応するワイヤエンコーディングに切り替わります。詳細は [stick に CJK 表示](#stick-に-cjk-表示オプション)。

## 仕組み

```
claude CLI ──PreToolUse/Stop/etc hooks──▶ Unix socket ──▶ daemon ──BLE NUS──▶ stick
                                                           ▲
                                                           └── ~/.claude/projects/*.jsonl を tail
                                                               トークン数と最近のメッセージを取得
```

* **Hooks**（`~/.claude/settings.json` で設定）はセッションのライフサイクルイベント、ツール呼び出し、権限要求、ターン境界で発火します。
* 各 hook は短命の Python スクリプトで、Unix socket 経由でイベントペイロードをローカルの **デーモン** に転送します。
* デーモンはセッションごとの状態（`total` / `running` / `waiting` / `tokens` / `entries`）を集約し、デスクトップアプリと同じ JSON ワイヤーフォーマットで BLE Nordic UART Service 経由でハートビートスナップショットを stick にプッシュします。
* 権限プロンプトでは hook が **ブロック** し、stick のボタンが結果を出すのを待ってから `allow` / `deny` を Claude Code に返します。

完全なワイヤープロトコルは
[buddy ファームウェアリポジトリの REFERENCE.md](https://github.com/anthropics/claude-desktop-buddy/blob/main/REFERENCE.md)
を参照してください。

## インストール

```bash
git clone https://github.com/SnowWarri0r/cc-buddy-bridge
cd cc-buddy-bridge
python3.12 -m venv .venv
.venv/bin/pip install -e .

# hooks を ~/.claude/settings.json に登録（先に .backup コピーを作成）：
.venv/bin/cc-buddy-bridge install

# 別ターミナルでデーモンを起動：
.venv/bin/cc-buddy-bridge daemon
```

**Windows ユーザー：** 上記コマンドの `.venv/bin/` をすべて `.venv\Scripts\` に置き換えてください。

その後、任意の `claude` セッションを起動します。デーモンは名前が `Claude` で始まる
BLE デバイスをスキャンし、接続後に状態のプッシュを開始します。

hooks を削除するには：

```bash
.venv/bin/cc-buddy-bridge uninstall
```

### ログイン時の自動起動

`cc-buddy-bridge daemon` を毎回手動で実行する代わりに、システムサービスとして
インストールするとログイン時に自動起動し、クラッシュ時には再起動します。

#### macOS（launchd）

ユーザーレベルの launchd エージェントとしてインストール：

```bash
.venv/bin/cc-buddy-bridge install --service
```

これは `~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist` を作成し、
インストールに使った venv の Python を指すように設定し、`launchctl load` で
即座に起動して stdout/stderr を `~/Library/Logs/cc-buddy-bridge.log` にリダイレクトします。

削除するには：

```bash
.venv/bin/cc-buddy-bridge uninstall --service
```

#### Windows（タスクスケジューラ）

タスクスケジューラのタスクとしてインストール：

```bash
.venv/Scripts/cc-buddy-bridge install --service
```

`cc-buddy-bridge-daemon` という名前のタスクを作成し、ログオン時に実行します。
ログは `%LOCALAPPDATA%\cc-buddy-bridge\daemon.log` に書き込まれます。

削除するには：

```bash
.venv/Scripts/cc-buddy-bridge uninstall --service
```

#### Linux（systemd）

同じ `--service` フラグが Linux ではユーザーレベルの systemd ユニットをインストールします：

```bash
.venv/bin/cc-buddy-bridge install --service
```

これは `~/.config/systemd/user/cc-buddy-bridge.service` を作成し、インストールに使った
venv の Python を指すように設定した上で、`systemctl --user daemon-reload` と
`systemctl --user enable --now cc-buddy-bridge.service` を実行して、デーモンを
即座に起動し以後ログインのたびに起動するようにします。ログは以下で確認できます：

```bash
journalctl --user -u cc-buddy-bridge.service -f
```

削除するには：

```bash
.venv/bin/cc-buddy-bridge uninstall --service
```

Linux 固有のはまりどころ：

* **BLE には BlueZ が必要。** `bluetooth` サービスが起動していること（`systemctl status bluetooth`）、ユーザーが `bluetooth` グループに入っていること（`sudo usermod -aG bluetooth $USER` のあとログアウトして再ログイン）を確認してください。これらが揃っていないと journal に `org.freedesktop.DBus.Error.ServiceUnknown ... org.bluez` が出ます。
* **ログアウト後も生存 / ブート時起動。** デフォルトでは user manager は最後のセッションと共に終了し、デーモンも止まります。ブート時に起動してログアウト後も残したい場合は、`loginctl enable-linger $USER` を一度実行してください。

Ubuntu 22.04 LTS で動作確認済み。systemd user manager のあるディストリビューション
（Fedora 39+、Debian 12+、Arch など）であれば動くはずです。あなたのディストリビューションで
調整が必要なら issue を立ててください。

---

`cc-buddy-bridge status` は hooks とサービスの両方の状態をまとめて報告します。

### IPC トランスポートのカスタマイズ

デーモンとフックスクリプトはローカル IPC で通信します。デフォルトで 99% は
カバーされますが、残り 1% のためにスイッチを 2 つ用意してあります：

| OS            | デフォルトトランスポート                   | `--socket` または `CC_BUDDY_BRIDGE_SOCK` で上書き |
| ------------- | ---------------------------------------- | ------------------------------------------------ |
| macOS / Linux | Unix socket `/tmp/cc-buddy-bridge.sock`  | 任意のパス、例：`~/cc-buddy.sock`                 |
| Windows       | TCP loopback `127.0.0.1:48765`           | 任意のポート、例：`:49000` または `127.0.0.1:49000` |

Windows でポート 48765 が他プロセスと衝突したら、
`cc-buddy-bridge daemon --socket :49000` を実行し、hud 呼び出しにも同じ
`--socket` を渡してください。あるいは `export CC_BUDDY_BRIDGE_SOCK=:49000`
で全フックスクリプトに対して一度に設定するのも OK。

### Claude Code のステータスラインに stick の状態を表示する

`cc-buddy-bridge hud` はバッテリー、暗号化状態、保留中の権限プロンプトを 1 行に
コンパクトにまとめて出力します。`~/.claude/settings.json` に組み込んでください：

```json
{
  "statusLine": {
    "type": "command",
    "command": "/path/to/.venv/bin/cc-buddy-bridge hud"
  }
}
```

ASCII 専用ターミナルの場合：`cc-buddy-bridge hud --ascii`。

すでに [claude-hud](https://github.com/jarrodwatts/claude-hud) や別のステータスライン
プラグインを使っていますか？ 両方を組み合わせられます——小さなシェルスクリプトで両者の
出力を連結するだけで OK。statusLine は複数行レスポンスを受け付けます。

iTerm2 での実機キャプチャ —— 肉球、バッテリーバー、暗号化ロック、当日のトークン数、当日のコスト、稼働中セッション数：

<p align="center"><img src="docs/img/statusline.png" alt="cc-buddy-bridge hud — 肉球、98% のバッテリーバー、ロック、101K トークン、$106.02、1 セッション稼働中" width="580"></p>

同じ行が遷移するその他の状態：

```
🐾 🔋 96% 🔒                          # リンクは暗号化、バッテリー良好
🐾 🔋 96% 🔒 12.3K $0.42              # 当日のトークン数（1K 以上）とコスト（$0.01 以上）
🐾 🔋 12% 🔒 1.2M $8.50 2run          # 低バッテリー、ヘビーな一日、稼働中セッション
🐾 ⚠ approve: Bash                    # stick に保留中の権限プロンプト
🐾 ∅                                  # stick 切断（デーモンは生きている）
🐾 off                                # デーモンが起動していない
```

トークン数は当日の `~/.claude/projects/*.jsonl` 内の `usage.output_tokens` の合計です。
コストは同じレコードから `input + output + キャッシュ読み書き × モデルごとのレート`
で見積もります。レート表は [`pricing.py`](src/cc_buddy_bridge/pricing.py) に
ハードコードされており、編集すれば上書き / 新モデル追加ができます。
請求の真実情報ではないので、目安として扱ってください。

## Claude Code の `permissions` 設定との組み合わせ

Claude Code 自身の `~/.claude/settings.json` の `permissions` ブロック
（`allow` / `ask` / `deny` リストと `defaultMode`）と、本ブリッジのスマートマッチャー
が両方で 1 回のツール呼び出しの挙動を決めます。挙動は明確ですが、正しい組み合わせを
選べるように整理しておきます。

各 `PreToolUse` イベントごとに：

```
matcher classify_command(hint)
 ├─ "allow"  → ブリッジが permissionDecision=allow を返す（ショートカット）
 ├─ "ask"    → ブリッジが stick のボタンを待つ → ボタン結果を返す
 └─ "default"→ ブリッジは判断を返さず → Claude Code の settings.json + defaultMode に委任
```

**推奨の組み合わせ**

| Claude Code `defaultMode` | Matcher `strict` | 挙動                                                                                                       |
| ------------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------- |
| `ask`（デフォルト）        | `false`          | 無害な bash はマッチャーが自動許可、危険な bash は stick へ、それ以外は Claude Code のターミナルプロンプト。  |
| `bypassPermissions`       | **`true`**       | **stick が唯一の人間確認ポイント。** 無害な bash は自動許可、それ以外（マッチ・非マッチ問わず）はすべて stick へ。 |
| `bypassPermissions`       | `false`          | ⚠ マッチャーの `always_ask` パターンだけが stick で止まり、それ以外は黙って自動承認されます。デーモン起動時にこの組み合わせを検出すると WARNING を出します。 |
| `auto`                    | `false`          | 実質 `ask` と同じ — マッチしないコマンドは Claude Code のフローへ落ちます。                                  |

`strict` は `~/.config/cc-buddy-bridge/matchers.toml` に記述：

```toml
strict = true
```

デーモン起動時、両方の設定を一行で要約してログに出すので、組み合わせミスがあれば
一目でわかります：

```
INFO cc_buddy_bridge.daemon: matcher: strict=False auto_allow=46 always_ask=53
INFO cc_buddy_bridge.daemon: settings.json: permissions.defaultMode='auto' ask=0
```

## 監査ログ

すべての `PreToolUse` の判定が JSONL ファイルに追記されるので、後から
「何が許可された / 拒否された / 委ねられた」を見返せます。`bypassPermissions`
モードでほとんどの判定が目に触れない場合に特に有用です。

デフォルトパス：

| OS      | パス                                                                |
| ------- | ------------------------------------------------------------------- |
| macOS   | `~/Library/Logs/cc-buddy-bridge-audit.jsonl`                        |
| Linux   | `$XDG_DATA_HOME/cc-buddy-bridge/audit.jsonl`（または `~/.local/share/...`）|
| Windows | `%LOCALAPPDATA%\cc-buddy-bridge\audit.jsonl`                        |

`CC_BUDDY_BRIDGE_AUDIT` 環境変数で上書き可能。

判定 1 件につき 1 行。フィールド：

```json
{"ts":"2026-05-16T00:15:12.690+08:00","session":"c461b71c","tool":"Bash",
 "hint":"git status -s","matcher":"allow","decision":"allow","source":"auto_allow"}
```

| フィールド  | 意味                                                                   |
| ----------- | ---------------------------------------------------------------------- |
| `ts`        | ISO-8601 ローカルタイムスタンプ（オフセット付き）                       |
| `session`   | Claude Code セッション ID の先頭 8 文字                                  |
| `tool`      | ツール名（`Bash`、`Edit` など）                                          |
| `hint`      | 実行内容の短いサマリ（200 文字に切り詰め）                                |
| `matcher`   | マッチャー分類：`allow` / `ask` / `default`                              |
| `decision`  | ブリッジが返した値：`allow` / `deny` / `null`（判定なし）                |
| `source`    | `auto_allow` / `stick` / `timeout` / `defer` / `ble_disconnected`       |
| `elapsed_s` | stick との往復秒数（stick が関わったときのみ）                            |

### 表示する

`cc-buddy-bridge audit` が公式のビューアです。カラー、整列、tail / フィルター / follow に対応：

```bash
cc-buddy-bridge audit                       # 直近 20 件
cc-buddy-bridge audit -n 100                # 直近 100 件
cc-buddy-bridge audit -f                    # 新着を追跡（Ctrl+C で終了）
cc-buddy-bridge audit --decision deny       # 拒否したものだけ
cc-buddy-bridge audit --source stick        # stick で判定したラウンドだけ
cc-buddy-bridge audit --tool Edit -n 50     # 直近 50 件の Edit 呼び出し
cc-buddy-bridge audit --path                # 監査ファイルパスを表示して終了
cc-buddy-bridge audit --ascii               # 色なし（パイプ / 非対応端末向け）
```

出力例：

```
# audit log: /Users/snow/Library/Logs/cc-buddy-bridge-audit.jsonl
00:21:09.029 Bash     —     defer       sleep 8 && gh run list --repo ...
00:30:10.212 Bash     allow auto_allow  cat >> tests/test_audit.py <<'EOF' ...
00:34:55.871 Bash     deny  stick       git push origin main --force
```

色：`allow` は緑、`deny` は赤、`—`（判定なし / 委任）はディム。source 列は `stick`（人がボタンを押した）が黄色、`timeout` が赤、それ以外はディム。

### 生の jq レシピ

サブコマンドを使わず jq で直接見たい場合：

```bash
# 今日 stick で拒否したもの
jq 'select(.decision=="deny")' ~/Library/Logs/cc-buddy-bridge-audit.jsonl

# 今週の自動許可コマンド頻度トップ
jq -r 'select(.source=="auto_allow") | .hint' ~/Library/Logs/cc-buddy-bridge-audit.jsonl \
  | awk '{print $1}' | sort | uniq -c | sort -rn | head
```

## 新バージョン通知

デーモンは毎日 1 回バックグラウンドで
`https://api.github.com/repos/SnowWarri0r/cc-buddy-bridge/releases/latest`
を取得し、キャッシュして二箇所で通知します：

* 起動時のデーモンログに 1 行（新バージョンがある場合のみ）
* `cc-buddy-bridge hud` がステータスライン末尾に `↑ vX.Y.Z`（`--ascii` モードでは
  `up vX.Y.Z`）を追加。色は黄色で控えめ、バッテリーやコストなど重要な情報を
  画面外に押し出さないようにしてあります。

明示的にチェック：

```bash
cc-buddy-bridge check-update
# Installed:   0.1.0
# Latest:      v0.1.2
#
# Update available: 0.1.0 → v0.1.2
# Pull with:        git pull && pip install -e .
# Then restart:     cc-buddy-bridge install --service  (or kickstart the daemon)
```

新バージョンがあれば終了コード `1`、なければ `0`。スクリプトで利用可能。

### 自動アップグレード

```bash
cc-buddy-bridge update            # y/N プロンプト後に実行
cc-buddy-bridge update -y         # プロンプト省略（CI / スクリプト向け）
```

リポジトリルートで `git pull && pip install -e .` を走らせ、続けてあなたが
インストールしたサービスバックエンド（launchd / systemd user unit /
Task Scheduler）でデーモンを再起動します。以下の場合は早期に安全に中止：

- git checkout でない（wheel 経由のインストール）—— pip での更新を促す
- 未コミットのローカル変更あり —— stash か commit を求める。作業を失う
  ことはありません
- tty でなく `-y` も無い —— 盲目的なプロンプトを拒否

サービスがインストールされていない場合（`cc-buddy-bridge daemon` を手動で
起動している）でもインストール自体は走り、最後に「デーモンを自分で再起動
してください」と案内します。

### プライバシー

1 日 1 回 api.github.com に HTTPS リクエスト。完全に無効化：

```bash
export CC_BUDDY_BRIDGE_NO_UPDATE_CHECK=1
```

キャッシュパス：macOS は `~/Library/Caches/cc-buddy-bridge/update_check.json`、
Linux は `$XDG_CACHE_HOME/cc-buddy-bridge/...`、
Windows は `%LOCALAPPDATA%\cc-buddy-bridge\update_check.json`。

ファームウェアのバージョン検出は対象外です —— stick の status ack に
ファームウェアバージョンフィールドがなく、上流
[anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy)
にも比較すべき release / tag がないためです。

## stick に CJK 表示（オプション）

stock ファームウェアは ASCII 限定の 5×7 フォントしか持たないため、
日本語 / 中国語 / 韓国語のコンテンツは stick 上で `?` の列になります
（[quirk #1](#1-utf-8-マルチバイト列が-ble-上で文字の途中で切られる)）。
上流の CONTRIBUTING は "新機能は受け付けない" と明記しているので、
CJK フォントを上流マージするルートはありません。代わりに
**フォーク専用のファームウェアビルド** を用意しました：
[github.com/SnowWarri0r/claude-desktop-buddy](https://github.com/SnowWarri0r/claude-desktop-buddy)
で自分でフラッシュすれば、stick が CJK を描画できるようになります。
stock ファームウェアのユーザーには影響しません —— ブリッジは明示的に
オプトインするまで、保守的な ASCII サニタイザーを維持します。

### 現状

| ターゲット | ファームウェアビルド | ブリッジ codec | 状態 |
| --- | --- | --- | --- |
| 簡体中国語 (zh-CN) | `m5stickc-plus-cjk-zh-cn` | `gbk` | ✅ 利用可能 — GB2312 ゾーン 1-55（記号 + Level 1 漢字）、~4300 字 |
| 繁体中国語 (zh-TW) | `m5stickc-plus-cjk-zh-tw` | `big5` | 🚧 計画中 — ブリッジ codec は配線済み、ファームビルドは簡体グリフ流用 |
| **日本語 (ja)** | `m5stickc-plus-cjk-ja` | `shift_jis` | ✅ **利用可能** — JIS X 0208 1-47 区（かな + Level 1 漢字）、~4400 字。半角カタカナは ASCII フォールバック |

### 有効化手順（簡体中国語 / 日本語）

下記は簡体中国語の例です。**日本語**の場合は `feat/cjk-display-ja` ブランチ、
`m5stickc-plus-cjk-ja` ビルド、`CC_BUDDY_CJK_TARGET=ja`（ワイヤ codec は `shift_jis`）
に読み替えてください。手順はまったく同じです。

1. **フォークの CJK ファームウェアをフラッシュ。**
   [SnowWarri0r/claude-desktop-buddy](https://github.com/SnowWarri0r/claude-desktop-buddy)
   を clone し、`feat/cjk-display-zh-cn` ブランチをチェックアウトして、
   `pio run -e m5stickc-plus-cjk-zh-cn -t upload --upload-port /dev/cu.usbserial-...`
   を実行（シリアルパスは自分の stick のものに置き換え）。CJK ビルドは
   firmware バイナリを ~120 KB 増やしますが、spiffs パーティション
   （GIF キャラパック用）には影響しません。

2. **ブリッジに GBK バイトで送信させる。** デーモンの環境変数に
   `CC_BUDDY_CJK_TARGET=zh-CN` を追加します。macOS:

   ```bash
   plutil -insert EnvironmentVariables.CC_BUDDY_CJK_TARGET -string zh-CN \
       ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
   launchctl bootout gui/$(id -u)/com.github.cc-buddy-bridge.daemon
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
   ```

   Linux: `~/.config/systemd/user/cc-buddy-bridge.service` を編集し、
   `[Service]` 配下に `Environment=CC_BUDDY_CJK_TARGET=zh-CN` を追加、
   `systemctl --user daemon-reload && systemctl --user restart cc-buddy-bridge.service` を実行。

3. **動作確認。** デーモンログに `cjk firmware target=zh-CN, wire codec=gbk`
   が出ます。次のアシスタントターンに中国語が含まれていれば、
   stick の画面に `?` ではなく実際の文字が描画されます。

stock 動作に戻すには env を削除し、デフォルトの `m5stickc-plus` ビルドを
再フラッシュしてください。

### 裏側で何が変わるか

- **ブリッジのワイヤ形式。** `sanitize_for_stick` が ASCII 限定から
  GBK エンコード可能に切り替わり、`encode()` はハートビート JSON を
  手動で組み立てて、文字列値を引用符の中に GBK バイトとして埋め込みます
  （キー、数値、構造記号は ASCII のまま）。stick の ArduinoJson 7 は
  これを受け入れます —— string 値の UTF-8 検証は行わないからです。
- **ファームウェアの描画。** スプライト対応の混合スクリプトレンダラー
  （`cjk_render.cpp`）がバイト列を走査し、ASCII (< 0x80) は
  `Fonts/ASC12.h` から 6×12 グリフを描き、GBK ペア（両バイト 0xA1-0xFE）
  は `Fonts/GB2312_L1.h` から 12×12 グリフを描きます。`drawHUD` は
  ピクセル & GBK 対応のラップを通すので、長いエントリは右端で切れずに
  きれいに折り返します。

### フォントクレジット

CJK ファームウェアバリアントは
[Fusion Pixel Font](https://github.com/TakWolf/fusion-pixel-font)
12px monospaced を使用しており、**SIL Open Font License 1.1** に基づきます。
OFL 全文と、Fusion Pixel が統合している上流フォント（Ark Pixel、Cubic 11、
Galmuri）の個別クレジットは、ファームウェアフォークの `src/Fonts/` に
同梱されています。@TakWolf さんに感謝。

### 注意点

- emoji（補助平面コードポイント）は GBK / Big5 / SJIS いずれの 2 バイト
  エンコーディングにも入っていません。CJK モードでも emoji は `?` に
  置き換えられます。
- Level-2 漢字（GB2312 ゾーン 56-87、稀少字）はフラッシュ容量節約のため
  同梱していません。描画時に `??` にフォールバックします。日常の中国語
  使用の 99% は Level 1 でカバーされます。
- フォークファームウェアには自動更新の仕組みがありません ——
  上流に変更があれば手動で pull して再フラッシュしてください。

## 動作環境

* macOS 12+ / Windows 10+ / BlueZ のある Linux
* Python 3.11+
* ファームウェア書き込み済みの claude-desktop-buddy（M5StickC Plus）
* Claude Code CLI

## シグナルマッピング

| Buddy フィールド   | ソース                                                |
| ----------------- | ----------------------------------------------------- |
| `total`           | `SessionStart` / `SessionEnd` フック                   |
| `running`         | `UserPromptSubmit` / 遅延 `Stop` フック                |
| `waiting`         | `PreToolUse` フック（決定保留中）                        |
| `prompt`          | `PreToolUse` フックのペイロード                          |
| `msg`             | 現在の状態から派生したサマリ                              |
| `entries`         | リアルタイム JSONL tailer（ユーザー入力 / ツール呼び出し / アシスタント発話） |
| `tokens`/`today`  | JSONL 内の `usage.output_tokens` の合計                  |

## 我々が踏んだファームウェアの罠（と回避方法）

参考ファームウェアにはワイヤープロトコルのドキュメントが警告していない鋭利な
エッジがいくつもあります。再びデバッグする羽目にならないよう、また本コードベースに
焼き付いている回避策の根拠が見えるように、ここに記録します。

### 1. UTF-8 マルチバイト列が BLE 上で文字の途中で切られる

CJK（や UTF-8 マルチバイト含む任意の内容）を含むハートビートは、
ATT Write-Without-Response の既定ペイロード上限（`MTU − 3` バイト、
通常 20 バイト）を簡単に超えます。
[`bleak`](https://github.com/hbldh/bleak) の `write_gatt_char()` は
write-without-response を **自動分割しません** —— あふれた分は黙って
落とされます。ファームウェアは UTF-8 多バイト列の **途中** で切れた
JSON を受け取ります（例：「你」の `0xE4 0xBD 0xA0` のうち先頭の
`0xE4` だけが届く）。ArduinoJson はパース失敗、TFT_eSPI の
`decodeUTF8()` ステートマシンは来ない継続バイトを待ち続けて固まり、
後続の正しいバイトも巻き込まれて誤読されます。レンダリング / BLE
タスクが詰まり、~1 秒後にリンクが切れて見えます。

**過去に踏んだ二つの誤診（やらかし回避用に記録）：**

- 最初は ASCII 限定の 5×7 GFX フォントのせいだと判断しました
  （`96740fd`）。CJK バイト列が **丸ごと** ファームウェアに届いて
  いれば正しい判断 —— でも届いていなかった。
- 次に `M5StickCPlus` ライブラリが 1.7 MB の HZK16 GB2312 フォントと
  `loadHzk16(InternalHzk16)` API を同梱していることに気づきました
  （`2099de1`）。事実だが症状とは無関係 —— 切られたバイトはフォント
  ルックアップに到達できません。

真の根本原因は [@omengye](https://github.com/omengye) さんのフォークで
診断されたもの。クレジットはそちらに。

**修正は [`182bfed`](https://github.com/SnowWarri0r/cc-buddy-bridge/commit/182bfed)
にてマージ済み**（PR [#14](https://github.com/SnowWarri0r/cc-buddy-bridge/pull/14)、
[@omengye](https://github.com/omengye) より）：`BuddyBLE.send()` は
`mtu_size − 3` で分割し、コードポイントの途中で切れないように
（`_utf8_safe_chunks`）動作します。`sanitize_for_stick()` は BMP を
すべてそのまま通し、補助面（emoji 大半）、サロゲート、C0/C1 制御文字
のみ `?` に置き換えます。フック側 stdin は OS コンソールの
コードページに関係なく UTF-8 として解釈されるため、Windows の `cp936`
ユーザーでも CJK の文字化けは起きません。

### 2. `entries` のワイヤー順は新しい順ではなく古い順

ファームウェアの `drawHUD` は `lines[nLines-1]` を最新（かつそれだけがハイライト
カラーとウィンドウ底部位置を得る）として扱います。新しい順で送ると、最新エントリは
ラップバッファの先頭に着地し、可視 3 行ウィンドウの外にクリップされます。

**回避：** デーモンは内部的に `state.entries` を新しい順に保ちます（安価な prepend）。
ハートビートをシリアライズするときは `reversed()` で逆順イテレートします。

### 3. `evt:"turn"` イベントは黙って捨てられる

REFERENCE.md は `turn` イベント形式を定義していますが、ファームウェアの
`_applyJson` はハートビートフィールド（`time`、`total`、`running`、`waiting`、
`tokens`、`tokens_today`、`msg`、`entries`、`prompt`）しか解析しません。
任意の `evt` ペイロードはパースされて捨てられます——エラーも、表示も無し。

**回避：** アシスタントの最初のテキストブロックを擬似的な `@ <text>` 行として
ハートビートの `entries` リストにミラーします。ファームウェアは既に `entries`
をレンダリングするので、プロトコル拡張は不要です。

### 4. Stop フックはアシスタントレコードがディスクに flush される前に発火する

Stop フックから transcript JSONL を読むと、**前の**ターンの内容が返ってきます——
Claude Code のディスクへの書き込みは非同期です。素直に Stop を使うと、すべての
`@`-entry が 1 ターン遅れます。

**回避：** Stop はコンテンツ抽出に一切使いません。JSONL tailer が `watchfiles`
で transcript ファイルを監視しており、新しいアシスタントレコードが着地した
瞬間（通常 <500 ms）に `on_assistant_text` コールバックを発火します。
コールバックがすぐにエントリを追加するので、ユーザーがターミナルを上にスクロール
する前に stick は返信を表示します。

### 5. 時計モードがターン終了時に transcript HUD を覆い隠す

ファームウェアは `running==0 && waiting==0 && on_USB_power` を満たした瞬間、
`drawHUD` を完全に飛ばして時計表示モードに入ります。私たちの古い `turn_end`
ハンドラは Claude が終わった瞬間に `running` を 0 にしていたため、emit したばかりの
`@` エントリが同じフレームで見えなくなっていました。

**回避：** `turn_end` は `asyncio.Task` をスケジュールし、15 秒スリープしてから
`running` を 0 に切り替えます。新しい `turn_begin` は保留中のタスクをキャンセルします。
stick は返信を読むのに十分な時間 HUD を表示し続け、本当のアイドルになってから時計に移ります。

### 6. LittleFS は自動フォーマットされない —— `push-character` は工場リセットまで失敗する

新しいファームウェアは `LittleFS.begin(false)`（マウント失敗時にフォーマットしない）
を呼びます。初期化されていないパーティションは 0/0 バイトでマウントされます。
`LittleFS.format()` を呼ぶ唯一のコードパスはデバイス上の **工場リセット** メニュー
（**A** 長押し → settings → reset → factory reset → 2 回タップ）です。

`cc-buddy-bridge push-character` はステータス ack 経由でこの状況を検出し、`ERROR`
レベルで対処方法のヒントを記録します。工場リセットは破壊的ですが（設定、統計、ボンドが消える）、
stick ごとに一度だけ必要です。

### 7. `blueutil --unpair` は新しめの macOS では当てにならない

クリーンな BLE ペアリングテストには両側のボンドをクリアする必要があります。
`blueutil` の `--unpair` は `EXPERIMENTAL` と表記されており、macOS Sonoma 以降では
キャッシュ済み LTK を実際に削除せずに成功を返します。その後の再接続は
`CBErrorDomain Code=14 "Peer removed pairing information"` で失敗します。

**回避：** `cc-buddy-bridge unpair` は暗号化チャンネル経由で stick 側をクリアしますが、
macOS 側はユーザーが手動で **システム設定 → Bluetooth → Claude-5C66 → ⓘ → このデバイスを忘れる**
を開く必要があります。その後、次回の再接続で新しい 6 桁の passkey ペアリングがトリガされます。

## ステータス

デイリードライバーとして完成しています —— 作者は Claude Code セッションのたびに動かしています。

**実戦投入済みのインフラ**

* 新規 BLE ペアリング — MITM + ボンディング + DisplayOnly passkey、エンドツーエンド検証済
* 再接続 — 指数バックオフ + 多重デーモンガード（別インスタンスが socket を保持していたら起動拒否）
* フォルダプッシュ — チャンク化されたフロー制御、1 パック上限 1.8 MB、チャンクごとの ack
* stick ステータスポーリング — バッテリー / 暗号化状態 / fs 空き容量を 60 秒ごと
* ロギング — ファイルローテーション、コンポーネント別レベル、構造化された権限往復トレース

**テスト + CI**

* state、protocol、installer、hud、matchers、JSONL tailer、フォルダプッシュ、各サービスバックエンド、BLE ラジオ復帰をカバーする 212 ユニットテスト
* GitHub Actions マトリクス（Python 3.11 / 3.12 / 3.13）

**Backlog**

* issue を立ててください —— 引っかかった粗い角、踏んだ罠、欲しい機能、挙動が変なプラットフォーム、なんでも

## コントリビュート

PR、バグ報告、「$ヘンな Linux ディストロで動かなかった」体験談、すべて歓迎です。
小さなバグ修正より大きな変更は、まず issue を立てて設計の方向を擦り合わせてから着手してください。

### 開発環境

```bash
git clone https://github.com/SnowWarri0r/cc-buddy-bridge
cd cc-buddy-bridge
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

`[dev]` extra で `pytest` + `ruff`（唯一の開発依存）が入ります。

### テストと lint

```bash
.venv/bin/pytest -q                  # ~210 件、1 秒以内に完走
.venv/bin/ruff check src/ tests/     # lint（CI が PR ごとに実行）
```

CI は **macOS / Linux / Windows × Python 3.11 / 3.12 / 3.13** のマトリクスで
テストを実行します。すべてのセルが緑にならないと PR は通りません。
ファイルシステムやパスを触る変更を入れると、Windows が真っ先に粗を炙り出します
（NTFS は POSIX mode bits を無視、バックスラッシュ vs スラッシュ、など）。

### ワイヤープロトコルに触る前に

stick ファームウェアには [7 つの記録済みの鋭い角](#我々が踏んだファームウェアの罠と回避方法) があります。
BLE の挙動が変な時は、`bleak` を追う前にまずそのセクションを目通ししてください。
「リンクが flap し続ける」系の多くは罠 #1（非 ASCII バイトで BLE スタックがクラッシュ）か
罠 #5（時計モードが HUD を奪う）に行き着きます。

### コミットメッセージ

題は短く、小文字、≤ 70 文字。本文で「なぜ」を一段落説明します。
`git log --oneline` を眺めれば調子がつかめます。
emoji は貼らないでください —— stick 側の sanitizer がどのみち剥がします。

### 翻訳

README は [English](README.md) / [简体中文](README.zh-CN.md) / [日本語](README.ja.md)
の三言語をミラーしています。ユーザー向けの文を触ったときは、可能なら三つとも同期してください。
無理なら PR 説明に「ほかの二言語は翻訳パスが必要」と書いてくれれば、フォローアップで対応します。

### ファームウェアの PR

buddy ファームウェアは [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy) にあります。
そちら側の変更は M5StickC Plus に書き込んでの検証が必須です —— ブリッジ側のモックでは
ワイヤープロトコルのズレを拾えません。PR 説明には「実機検証済み」と「まだ理論段階」を
明示的に書き分けてください。レビュアーは差分だけでは判別できません。

## Star ヒストリー

<a href="https://star-history.com/#SnowWarri0r/cc-buddy-bridge&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=SnowWarri0r/cc-buddy-bridge&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=SnowWarri0r/cc-buddy-bridge&type=Date" />
    <img alt="SnowWarri0r/cc-buddy-bridge の star 履歴グラフ" src="https://api.star-history.com/svg?repos=SnowWarri0r/cc-buddy-bridge&type=Date" />
  </picture>
</a>

## ライセンス

MIT。[LICENSE](LICENSE) を参照。
