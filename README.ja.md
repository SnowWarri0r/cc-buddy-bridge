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
- **ステータスライン** —— `cc-buddy-bridge hud` がプロンプトバーにバッテリー / 暗号化状態 / 保留中の権限プロンプトを表示します。[claude-hud](https://github.com/jarrodwatts/claude-hud) と並べて使うことも可能。
- **ワンコマンドのインストール + 自動起動** —— `cc-buddy-bridge install --service` が OS ごとに正しいバックエンドを選びます（macOS は launchd、Linux は systemd ユーザーユニット、Windows はタスクスケジューラ）。
- **カスタム GIF キャラクター** —— `cc-buddy-bridge push-character ./pack/` でフレームの入ったフォルダを BLE 経由でアップロードします。チャンク化されたフロー制御つき。

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

iTerm2 での実機キャプチャ —— 肉球、バッテリープログレスバー、暗号化ロック、稼働中セッション数：

<p align="center"><img src="docs/img/statusline.png" alt="cc-buddy-bridge hud — 肉球、緑色フルのバッテリーバー、100%、ロック、1run" width="436"></p>

同じ行が遷移するその他の状態：

```
🐾 🔋 96% 🔒              # リンクは暗号化、バッテリー良好
🐾 🔋 12% 🔒 2run         # 低バッテリー、セッションが稼働中
🐾 ⚠ approve: Bash        # stick に保留中の権限プロンプト
🐾 ∅                      # stick 切断（デーモンは生きている）
🐾 off                    # デーモンが起動していない
```

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

### 1. 非 ASCII バイトが BLE スタックをクラッシュさせる

5×7 の Adafruit GFX ビットマップフォントテーブルは ASCII 専用です。
`0x80`–`0xFF` のバイト（つまり UTF-8 の継続バイトと emoji の先頭バイト全て）は
グリフテーブルの範囲外をインデックスし、十分な数のコードパスでハートビート
書き込みから ~1 秒以内に radio タスクをハードリセットします。

**回避：** `protocol.py` の `sanitize_for_stick()` が送信前に `0x20`–`0x7E`
（および tab）以外のすべてを `?` に書き換えます。CJK ユーザーは stick 上で
`?` の列が並ぶことになります。可逆性は失われますが安定します。

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

* state、protocol、installer、hud、matchers、JSONL tailer、フォルダプッシュ、各サービスバックエンドをカバーする 98 ユニットテスト
* GitHub Actions マトリクス（Python 3.11 / 3.12 / 3.13）

**Backlog**

* issue を立ててください —— 引っかかった粗い角、踏んだ罠、欲しい機能、挙動が変なプラットフォーム、なんでも

## ライセンス

MIT。[LICENSE](LICENSE) を参照。
