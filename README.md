# J-REIT 配当利回り監視 Bot

GitHub Actions で毎日自動実行し、東証REIT指数ETF（1343.T）の配当利回りが閾値を超えたときに Discord へ通知する Bot です。

## 機能

- **閾値超過通知**：配当利回りが閾値を上回った／下回ったタイミングで通知
- **週次リマインダー**：閾値超過が続く間、毎週土曜日にリマインド通知
- **Baseline 自動更新**：年越し時に前年の実績を取り込み、閾値を自動調整
- **土日・祝日対応**：市場休場日はエラー通知を抑制。土曜日はキャッシュデータでリマインダーを送信
- **状態の永続化**：実行結果を `data/state_jp.json` に保存し、GitHub Actions が自動コミット

## ディレクトリ構成

```
.
├── .github/workflows/
│   └── monitor.yml              # GitHub Actions ワークフロー（毎日 JST 20:00 実行）
├── data/
│   └── state_jp.json            # 実行状態の保存ファイル（自動更新）
├── src/
│   ├── J-REIT_monitor.py        # メイン監視スクリプト
│   ├── config_jp.py             # 監視対象・閾値の設定
│   └── calculate_baseline_jp.py # Baseline 手動計算ユーティリティ
├── requirements.txt
└── README.md
```

## セットアップ

### 1. リポジトリをフォーク

このリポジトリをご自身の GitHub アカウントにフォークします。

### 2. Discord Webhook URL を取得

通知先の Discord チャンネルで：
「チャンネルの編集」→「連携サービス」→「ウェブフック」→「新しいウェブフック」を作成し、URL をコピーします。

### 3. GitHub Secret を設定

フォーク先リポジトリの **Settings → Secrets and variables → Actions** で以下を登録します。

| Name | Value |
|------|-------|
| `DISCORD_WEBHOOK_URL` | コピーした Webhook URL |

### 4. Actions を有効化

フォーク直後は Actions が無効になっている場合があります。
リポジトリの **Actions タブ** → "I understand my workflows, go ahead and enable them" をクリックして有効化してください。

これで毎日 JST 20:00 に自動実行されます。**Actions タブ → Run workflow** から手動実行も可能です。

## 設定変更

`src/config_jp.py` を編集します。

```python
ETFS = {
    "1343.T": {
        "name": "NEXT FUNDS 東証REIT指数連動型上場投信",
        "baseline_years": 12,       # Baseline の年数
        "baseline_yield": 4.22,     # Baseline 利回り（%）
        "baseline_year_end": 2024,  # Baseline の最終年
        "threshold_offset": 0.0,    # 閾値 = baseline + offset
    },
}
```

- `threshold_offset` を上げると閾値が高くなり、通知が出にくくなります
- 複数銘柄を監視したい場合は ETFS に追記します

## Baseline の仕組み

「Baseline」とは過去の平均利回りのことです。
閾値は `baseline_yield + threshold_offset` で計算されます。

- **初回起動時**：`config_jp.py` の値をそのまま使用
- **年越し時**：前年の実績（分配金総額 ÷ 年末株価）を自動取得し、移動平均でBaselineを更新
- **欠落年がある場合**：過去データを自動補完

Baseline を手動で再計算したい場合は `src/calculate_baseline_jp.py` を使います。

```bash
python src/calculate_baseline_jp.py
```

## Discord 通知の種類

| 種類 | タイミング | 色 |
|------|-----------|-----|
| 監視開始 | 初回起動時 | 青 |
| 監視開始（閾値超過中） | 初回起動時点で超過 | オレンジ |
| 閾値上抜け | below → above | 緑 |
| 閾値下抜け | above → below | 赤 |
| 週次リマインダー | 超過継続中の毎週土曜 | 黄 |
| データ取得失敗 | 平日にAPIエラー | 赤 |
| Baseline 更新 | 年越し時 | 紫 |

## 使用ライブラリ

- [yfinance](https://github.com/ranaroussi/yfinance) — 株価・配当データの取得
- [requests](https://docs.python-requests.org/) — Discord Webhook への送信
