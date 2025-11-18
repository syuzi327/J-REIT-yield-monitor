"""
ETF監視Bot設定ファイル（1343 JP版）

ロジック:
- 1343.T (東証REIT) の円建てデータを監視
- 閾値 = baseline_yield + threshold_offset（年度内固定）
- baseline更新は年越し初回実行時に自動実行（前年の実績を計算して反映）
- 前年の利回り = その年の分配金総額 ÷ 年末の株価
- 欠落期間がある場合は自動補完（初回起動時も対応）
- 週次リマインダーは毎週土曜日に送信
"""

# 監視対象ETF
ETFS = {
    "1343.T": {
        "name": "NEXT FUNDS 東証REIT指数連動型上場投信",
        "inception_date": "2008-09-18",
        # calculate_baseline_jp.pyにて取得
        "baseline_years": 12,          # 2013-2024年
        "baseline_yield": 4.22,        # 2013-2024年の平均利回り（%）
        "baseline_year_end": 2024,     # baselineの最終年 
        "threshold_offset": 0.0,       # baseline + 0.0%で通知
    },
    # 必要に応じて他の日本株銘柄（例: 1489.T など）を追加可能
}

# データファイルパス (日本版専用)
STATE_FILE_JP = "data/state_jp.json"

# Discord Webhook URL（環境変数から取得）
# 米国版と同じ DISCORD_WEBHOOK_URL を参照します