"""
ETF配当利回り監視Bot（1343 円建て専用）- 最終版

ロジック:
- 1343.T (東証REIT) の円建てデータを監視
- 為替レート計算は不要
- TTM方式で毎日の利回りを取得（信頼性が高い）
- 年越し初回実行時のみ前年実績を計算してbaseline更新
- 欠落期間がある場合は過去データを自動補完
- 取引なしの日はstate更新をスキップ（配当落ち異常値の回避）
"""

import os
import sys
import json
import shutil
import time
import yfinance as yf
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

# スクリプトのディレクトリをパスに追加
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

# 日本版の設定ファイルを読み込む
from config_jp import ETFS, STATE_FILE_JP as STATE_FILE

# 日本時間タイムゾーン
JST = timezone(timedelta(hours=9))


def _with_retry(fn, *args, retries=3, delay=5):
    """None以外の結果が得られるまでリトライ"""
    for attempt in range(retries):
        result = fn(*args)
        if result is not None:
            return result
        if attempt < retries - 1:
            print(f"  ⏳ {delay}秒後にリトライ ({attempt + 1}/{retries - 1})...")
            time.sleep(delay)
    return None


def iso_to_date(s):
    """ISO形式の日付文字列をdate型に変換"""
    return datetime.fromisoformat(s).date()


def _etf_data_from_state(prev_state):
    """保存された状態からETFデータを復元"""
    return {
        "yield": prev_state.get("current_yield", 0),
        "price_jpy": prev_state.get("price_jpy", 0),
        "dividend_jpy": prev_state.get("dividend_jpy", 0),
        "last_trade_date": prev_state.get("last_trade_date"),
    }


def _build_comparison_data(prev_state):
    """リマインダー通知用の比較データを構築"""
    is_first = prev_state.get("last_reminded") == prev_state.get("crossed_above_date")
    return {
        "crossed_above_yield":     prev_state.get("crossed_above_yield"),
        "crossed_above_price_jpy": prev_state.get("crossed_above_price_jpy"),
        "last_reminded_yield":     None if is_first else prev_state.get("last_reminded_yield"),
        "last_reminded_price_jpy": None if is_first else prev_state.get("last_reminded_price_jpy"),
    }


def _check_saturday_reminder(prev_state, today):
    """
    土曜日リマインダーを送るべきか判定（データ取得失敗時フォールバック兼用）

    Returns:
        tuple: (should_remind: bool, days_above: int)
    """
    if today.weekday() != 5:
        return False, 0
    if prev_state.get("status") != "above":
        return False, 0

    crossed_above_date = prev_state.get("crossed_above_date")
    days_above = (today - iso_to_date(crossed_above_date)).days if crossed_above_date else 0

    return True, days_above


def get_etf_data(ticker):
    """ETFの配当利回りと価格を取得（TTM方式 - 信頼性高）- 円建て"""
    try:
        etf = yf.Ticker(ticker)

        # historyから価格を取得
        history = etf.history(period="5d")

        if history.empty:
            print(f"{ticker} 履歴データなし")
            return None

        # Volume=0のエントリを除外（週末実行時に翌営業日の幽霊エントリが混入する対策）
        history = history[history["Volume"] > 0]

        if history.empty:
            print(f"{ticker} 有効な取引データなし（Volume=0のみ）")
            return None

        # 最新の価格
        current_price = history["Close"].iloc[-1]
        last_trade_date = history.index[-1].date().isoformat()

        # 配当情報を取得（TTM方式）
        try:
            dividends = etf.dividends
            if not dividends.empty:
                # 400日ウィンドウで取得して直近4回分に絞る
                # （365日境界で四半期配当が脱落する誤検知を防ぐ）
                four_hundred_days_ago = history.index[-1] - timedelta(days=400)
                recent_dividends = dividends[dividends.index > four_hundred_days_ago]
                if len(recent_dividends) > 4:
                    recent_dividends = recent_dividends.iloc[-4:]
                annual_dividend = recent_dividends.sum()
                # yfinanceの新バージョンではSeriesが返る場合があるためスカラーに変換
                if hasattr(annual_dividend, 'squeeze'):
                    annual_dividend = float(annual_dividend.squeeze())
                dividend_yield = (annual_dividend / current_price) * 100
            else:
                # 配当データがない場合はinfoから取得（fallback）
                info = etf.info
                dividend_yield = (info.get("dividendYield") or 0) * 100
                # dividendRateは .T 銘柄ではTTMでない場合があるため、yieldから逆算
                annual_dividend = (dividend_yield / 100) * current_price
        except Exception:
            dividend_yield = 0
            annual_dividend = 0

        return {
            "yield": round(dividend_yield, 2),
            "price_jpy": round(current_price, 2),
            "dividend_jpy": round(annual_dividend, 2),
            "last_trade_date": last_trade_date,
        }
    except Exception as e:
        print(f"{ticker} データ取得エラー: {e}")
        return None


def get_current_threshold(ticker, config, state):
    """
    現在の閾値を取得（baselineから計算）

    Returns:
        dict: threshold情報
    """
    threshold_offset = config["threshold_offset"]

    # state.jsonからbaselineを取得
    if ticker in state and "baseline" in state[ticker]:
        baseline_years = state[ticker]["baseline"]["years"]
        baseline_yield = state[ticker]["baseline"]["yield"]
        print(f"   📊 Baseline読み込み: {baseline_yield:.2f}% ({baseline_years}年)")
    else:
        # 初回はconfigから取得
        baseline_years = config["baseline_years"]
        baseline_yield = config["baseline_yield"]
        print(f"   🆕 初回実行: Baseline = {baseline_yield:.2f}% ({baseline_years}年)")

    # 閾値 = baseline + offset
    threshold = baseline_yield + threshold_offset

    return {
        "threshold": round(threshold, 2),
        "baseline_years": baseline_years,
        "baseline_yield": round(baseline_yield, 2),
    }


def should_update_baseline(ticker, state, config):
    """
    baselineを更新すべきか判定

    Returns:
        tuple: (should_update: bool, last_year: int, is_initial: bool)
    """

    current_year = datetime.now(JST).year

    # 初回起動の場合
    if ticker not in state or "last_year" not in state[ticker]:
        # config.pyの baseline_year_end（baselineの最終年）を取得
        baseline_year_end = config.get("baseline_year_end", current_year - 1)

        # 初回起動でも欠落がある場合は補完が必要
        # baseline_year_endの次の年から補完開始（二重計上を防ぐ）
        if baseline_year_end < current_year - 1:
            print(f"   🆕 初回起動: {baseline_year_end}年以降のデータ欠落を検知")
            return True, baseline_year_end, True

        return False, None, True  # 初回起動だが補完不要

    last_year = state[ticker]["last_year"]

    # すでに今年のデータで更新済み（年度更新の重複実行を防ぐ）
    if last_year == current_year:
        return False, None, False

    # 年が変わっている場合（前年のデータで更新）
    if last_year < current_year:
        return True, last_year, False

    return False, None, False


def get_next_reminder_saturday(base_date):
    """
    次回のリマインダー土曜日を取得

    Args:
        base_date: 基準日（date型またはISO文字列）

    Returns:
        str: 次回リマインダー日（ISO形式）
    """

    # 文字列の場合はdateに変換
    if isinstance(base_date, str):
        base_date = iso_to_date(base_date)

    # 基準日から7日後
    seven_days_later = base_date + timedelta(days=7)

    # 7日後が土曜日なら、その日が次回
    if seven_days_later.weekday() == 5:
        return seven_days_later.isoformat()

    # そうでなければ、7日後以降の最初の土曜日を探す
    days_until_saturday = (5 - seven_days_later.weekday()) % 7
    next_saturday = seven_days_later + timedelta(days=days_until_saturday)
    return next_saturday.isoformat()


def get_year_average_from_history(ticker, year):
    """
    過去の年度の平均利回りを取得（年度更新時・欠落データ補完用）

    計算方法: その年の分配金総額 ÷ 年末の株価

    Args:
        ticker: ETFティッカーシンボル
        year: 対象年

    Returns:
        float or None: 年間平均利回り
    """
    try:
        etf = yf.Ticker(ticker)

        start = f"{year}-01-01"
        end = f"{year}-12-31"
        end_for_history = f"{year+1}-01-01"

        print(f"     📊 {year}年のデータを取得中... ({start} ～ {end})")

        # 履歴データ取得（end は翌年1/1を指定して年末最終営業日を確実に含める）
        history = etf.history(start=start, end=end_for_history)

        if history.empty:
            print(f"     ⚠️ 履歴データ取得失敗")
            return None

        # 年末の株価を取得
        year_end_price = history["Close"].iloc[-1]

        # その年の分配金総額を取得
        try:
            dividends = etf.dividends
            if not dividends.empty:
                # その年の配当を取得
                year_dividends = dividends[dividends.index.year == year]

                if not year_dividends.empty:
                    # 年間分配金総額
                    annual_dividend = year_dividends.sum()

                    # 利回り = 年間分配金総額 ÷ 年末株価
                    dividend_yield = (annual_dividend / year_end_price) * 100

                    print(f"     ✅ {year}年: 分配金 ¥{annual_dividend:.2f}, 年末株価 ¥{year_end_price:.2f}, 利回り {dividend_yield:.2f}%")
                    return round(dividend_yield, 2)
                else:
                    print(f"     ⚠️ {year}年: 分配金データなし")
                    return None
            else:
                print(f"     ⚠️ {year}年: 分配金データなし（配当履歴なし）")
                return None
        except Exception as e:
            print(f"     ⚠️ {year}年: 分配金データ取得エラー: {e}")
            return None

    except Exception as e:
        print(f"     ⚠️ {year}年: データ取得エラー: {e}")
        return None


def update_baseline(ticker, last_year, state, config, is_initial=False):
    """
    baselineを更新（年度更新時に前年の実績を反映）

    Args:
        ticker: ETFティッカー
        last_year: 前年の年度（初回起動時はbaseline_year_end）
        state: 現在の状態
        config: 設定
        is_initial: 初回起動かどうか

    Returns:
        tuple: (result_dict | None, errors_list)
               errors_list = [{"reason": "...", "baseline_data": {...}}, ...]
    """
    errors = []
    current_year = datetime.now(JST).year

    # 現在のbaselineを取得
    if ticker in state and "baseline" in state[ticker]:
        baseline_years = state[ticker]["baseline"]["years"]
        baseline_yield = state[ticker]["baseline"]["yield"]
    else:
        baseline_years = config["baseline_years"]
        baseline_yield = config["baseline_yield"]

    old_baseline = {
        "years": baseline_years,
        "yield": baseline_yield
    }

    # 初回起動の場合: baseline_year_end + 1年から開始（二重計上を防ぐ）
    if is_initial:
        start_year = last_year + 1  # baseline_year_endの次の年から
        print(f"   🆕 初回起動: {start_year}年以降のデータを補完します")
    else:
        start_year = last_year
        # 前年の実績を計算（通常の年度更新）
        print(f"   📅 前年({last_year}年)の実績を計算中...")
        last_year_avg = _with_retry(get_year_average_from_history, ticker, last_year)

        if last_year_avg is None:
            print(f"   ⚠️ 前年データ取得失敗 - baseline更新をスキップ")
            errors.append({
                "reason": f"{last_year}年の実績データ取得に失敗したため、Baselineの自動更新をスキップしました。現在のBaselineで監視を続行します。",
                "baseline_data": old_baseline
            })
            return None, errors

        # baselineを更新
        new_baseline_yield = (baseline_yield * baseline_years + last_year_avg) / (baseline_years + 1)
        new_baseline_years = baseline_years + 1

        print(f"   📈 Baseline更新: {baseline_yield:.2f}% ({baseline_years}年) → {new_baseline_yield:.2f}% ({new_baseline_years}年)")
        print(f"     {last_year}年実績: {last_year_avg:.2f}% を反映")

        # 更新後の値を使用
        baseline_yield = new_baseline_yield
        baseline_years = new_baseline_years
        start_year = last_year + 1
        last_year_reported_avg = last_year_avg

    # 欠落データの補完（初回起動または複数年飛ばした場合）
    years_gap = current_year - start_year
    last_successful_year = None
    last_supplementary_avg = None
    if years_gap > 0:
        if years_gap > 1 or is_initial:
            if is_initial:
                print(f"   ⚠️ {years_gap}年分のデータが欠落 → 自動補完を試行")
            else:
                print(f"   ⚠️ {years_gap - 1}年分のデータが欠落 → 自動補完を試行")

        # 欠落した年を順番に処理
        for year in range(start_year, current_year):
            print(f"   📅 {year}年のデータを補完中...")

            year_avg = _with_retry(get_year_average_from_history, ticker, year)

            if year_avg is not None:
                # baselineを更新
                new_baseline_yield = (baseline_yield * baseline_years + year_avg) / (baseline_years + 1)
                new_baseline_years = baseline_years + 1
                baseline_yield = new_baseline_yield
                baseline_years = new_baseline_years
                print(f"     ✅ {year}年: {year_avg:.2f}% → Baseline更新: {baseline_yield:.2f}% ({baseline_years}年)")

                # 最後に成功した年を記録
                last_successful_year = year
                last_supplementary_avg = year_avg
            else:
                print(f"     ⚠️ {year}年: データ取得失敗 - スキップ")
                errors.append({
                    "reason": f"欠落データ補完: {year}年の実績データ取得に失敗しました。この年のデータをスキップしてBaseline更新を続行します。",
                    "baseline_data": {"years": baseline_years, "yield": round(baseline_yield, 2)}
                })

    # 更新結果を返す
    if is_initial:
        # 初回起動: 全年失敗の場合は更新なしとして扱う
        if last_successful_year is None:
            print(f"   ⚠️ 全年のデータ取得に失敗 - baseline更新をスキップ")
            return None, errors
        return {
            "years": baseline_years,
            "yield": round(baseline_yield, 2),
            "old_baseline": old_baseline,
            "last_year": last_successful_year,
            "last_year_avg": last_supplementary_avg,
        }, errors
    else:
        # 通常更新: last_year_avg は補完ループで上書きされない
        return {
            "years": baseline_years,
            "yield": round(baseline_yield, 2),
            "old_baseline": old_baseline,
            "last_year": last_year,
            "last_year_avg": last_year_reported_avg,
        }, errors


def load_state():
    """状態ファイルを読み込み（エラー保護付き）"""
    if not STATE_FILE.startswith('/'):
        state_path = script_dir.parent / STATE_FILE
    else:
        state_path = Path(STATE_FILE)

    if state_path.exists():
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"⚠️ state_jp.jsonが壊れています: {e}")
            print(f"   バックアップを作成して初期化します...")

            backup_path = state_path.with_suffix(".json.backup")
            shutil.copy(state_path, backup_path)
            print(f"   バックアップ: {backup_path}")

            return {}
        except Exception as e:
            print(f"⚠️ state_jp.json読み込みエラー: {e}")
            return {}
    return {}


def save_state(state):
    """状態ファイルを保存"""
    if not STATE_FILE.startswith('/'):
        state_path = script_dir.parent / STATE_FILE
    else:
        state_path = Path(STATE_FILE)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ state_jp.json保存エラー: {e}")


def should_notify(ticker, current_yield, threshold, state, etf_data):
    """
    通知すべきかを判定

    Returns:
        tuple: (should_notify: bool, notification_type: str, reason: str)
    """

    today = datetime.now(JST).date()
    last_trade_date = etf_data.get("last_trade_date")

    # 初回実行
    if ticker not in state:
        if current_yield >= threshold:
            return True, "initial_above", f"初回起動時点で閾値を上回っています: {current_yield:.2f}% ≥ {threshold:.2f}%"
        else:
            return True, "initial", "初回起動"

    prev_state = state[ticker]
    prev_status = prev_state.get("status", "below")
    prev_yield = prev_state.get("current_yield", 0)
    last_update_date = prev_state.get("last_trade_date")

    # 閾値超過中の週次リマインダー（土曜日のみ）
    if prev_status == "above" and current_yield >= threshold:
        should_remind, days_above = _check_saturday_reminder(prev_state, today)
        if should_remind:
            return True, "reminder", f"週次リマインダー（土曜日、継続{days_above}日目）"

    # 取引日チェック: 前回と同じ日付なら更新しない（土日・祝日・配当落ち異常値対策）
    if last_trade_date and last_trade_date == last_update_date:
        print(f"   💤 取引なし（前回: {last_update_date}）- データ更新スキップ")
        return False, "no_trade", "取引日なし"

    # 通常の上抜け検知
    if prev_status == "below" and current_yield >= threshold:
        return True, "crossed_above", f"閾値上抜け: {prev_yield:.2f}% → {current_yield:.2f}%"

    # 通常の下抜け検知
    if prev_status == "above" and current_yield < threshold:
        return True, "crossed_below", f"閾値下抜け: {prev_yield:.2f}% → {current_yield:.2f}%"

    return False, None, "通知不要"


def _create_error_embed(notification_type, ticker, reason, baseline_data=None):
    """エラー通知用のEmbed作成"""
    color_map = {
        "error_etf_data": 0xFF0000,
        "error_baseline": 0xFF9900,
    }
    title_map = {
        "error_etf_data": "❌ データ取得失敗",
        "error_baseline": "❌ Baseline更新失敗",
    }
    etf_name = ETFS[ticker]["name"]
    embed = {
        "title": f"{title_map[notification_type]} - {ticker}",
        "description": f"**{etf_name}**",
        "color": color_map[notification_type],
        "fields": [
            {
                "name": "📝 詳細",
                "value": reason,
                "inline": False
            }
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "ETF利回り監視Bot (JP)"}
    }

    # Baseline更新失敗時は追加情報
    if notification_type == "error_baseline" and baseline_data:
        embed["fields"].insert(0, {
            "name": "ℹ️ 現在のBaseline",
            "value": f"{baseline_data['yield']}% ({baseline_data['years']}年)",
            "inline": False
        })

    return embed


def _create_baseline_updated_embed(ticker, threshold, reason, baseline_data, old_baseline):
    """Baseline更新通知用のEmbed作成"""
    etf_name = ETFS[ticker]["name"]
    embed = {
        "title": f"📊 Baseline自動更新 - {ticker}",
        "description": f"**{etf_name}**",
        "color": 0x9966FF,
        "fields": [
            {
                "name": "📈 更新前",
                "value": f"{old_baseline['yield']}% ({old_baseline['years']}年)",
                "inline": True
            },
            {
                "name": "📈 更新後",
                "value": f"**{baseline_data['yield']}%** ({baseline_data['years']}年)",
                "inline": True
            },
            {
                "name": "🎯 新しい閾値",
                "value": f"{threshold}%",
                "inline": True
            },
            {
                "name": "📝 詳細",
                "value": reason,
                "inline": False
            }
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "ETF利回り監視Bot (JP)"}
    }
    return embed


def _create_normal_embed(notification_type, ticker, etf_data, threshold, reason,
                         baseline_data=None, comparison_data=None):
    """通常通知用のEmbed作成（上抜け・下抜け・リマインダー・初回）- 円建て専用"""
    color_map = {
        "crossed_above": 0x00FF00,
        "crossed_below": 0xFF0000,
        "reminder":      0xFFFF00,
        "initial":       0x0099FF,
        "initial_above": 0xFF6600,
    }
    title_map = {
        "crossed_above": "🚀 利回り閾値上抜け！",
        "crossed_below": "📉 利回り閾値下抜け",
        "reminder":      "📌 週次リマインダー",
        "initial":       "✅ 監視開始",
        "initial_above": "⚠️ 監視開始（閾値超過中）",
    }

    etf_name = ETFS[ticker]["name"]
    price_jpy = etf_data["price_jpy"]
    dividend_jpy = etf_data["dividend_jpy"]

    fields = [
        {
            "name": "📊 配当利回り (TTM)",
            "value": f"**{etf_data['yield']}%**",
            "inline": True
        },
        {
            "name": "🎯 閾値",
            "value": f"{threshold}%",
            "inline": True
        }
    ]

    # 初回起動時はBaseline情報を追加
    if notification_type in ["initial", "initial_above"] and baseline_data:
        fields.append({
            "name": "ℹ️ Baseline",
            "value": f"{baseline_data['yield']}% ({baseline_data['years']}年)",
            "inline": True
        })

        # initial_aboveの場合は次回リマインダー日を追加
        if notification_type == "initial_above":
            today = datetime.now(JST).date()
            next_saturday = get_next_reminder_saturday(today)
            fields.append({
                "name": "📅 次回リマインダー",
                "value": f"{next_saturday} (土曜日)",
                "inline": False
            })

    # リマインダーの比較フィールド
    if notification_type == "reminder" and comparison_data:
        current_yield_val = etf_data["yield"]
        current_price_val = etf_data["price_jpy"]
        crossed_above_yield = comparison_data.get("crossed_above_yield")
        crossed_above_price = comparison_data.get("crossed_above_price_jpy")
        last_reminded_yield = comparison_data.get("last_reminded_yield")
        last_reminded_price = comparison_data.get("last_reminded_price_jpy")

        if crossed_above_yield is not None:
            yd = current_yield_val - crossed_above_yield
            pd = current_price_val - crossed_above_price
            fields.append({
                "name": "📈 上抜け時比",
                "value": (
                    f"利回り {'+'if yd>=0 else ''}{yd:.2f}%"
                    f" ({crossed_above_yield:.2f}% → {current_yield_val:.2f}%)\n"
                    f"価格 {'+'if pd>=0 else ''}¥{pd:,.0f}"
                    f" (¥{crossed_above_price:,.0f} → ¥{current_price_val:,.0f})"
                ),
                "inline": False,
            })

        if last_reminded_yield is not None:
            yd = current_yield_val - last_reminded_yield
            pd = current_price_val - last_reminded_price
            fields.append({
                "name": "📅 前週比",
                "value": (
                    f"利回り {'+'if yd>=0 else ''}{yd:.2f}%"
                    f" ({last_reminded_yield:.2f}% → {current_yield_val:.2f}%)\n"
                    f"価格 {'+'if pd>=0 else ''}¥{pd:,.0f}"
                    f" (¥{last_reminded_price:,.0f} → ¥{current_price_val:,.0f})"
                ),
                "inline": False,
            })

    # 価格情報 (JPYのみ)
    fields.extend([
        {
            "name": "💴 現在価格",
            "value": f"¥{price_jpy:,.0f}",
            "inline": True
        },
        {
            "name": "💰 年間配当 (TTM)",
            "value": f"¥{dividend_jpy:,.0f}",
            "inline": True
        },
        {
            "name": "📝 詳細",
            "value": reason,
            "inline": False
        }
    ])

    embed = {
        "title": f"{title_map[notification_type]} - {ticker}",
        "description": f"**{etf_name}**",
        "color": color_map[notification_type],
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "ETF利回り監視Bot (JP)"}
    }

    return embed


def create_discord_embed(notification_type, ticker, etf_data, threshold, reason,
                         baseline_data=None, old_baseline=None, comparison_data=None):
    """Discord埋め込みメッセージを作成 (円建て専用)"""
    if notification_type in ("error_etf_data", "error_baseline"):
        return _create_error_embed(notification_type, ticker, reason, baseline_data=baseline_data)
    if notification_type == "baseline_updated":
        return _create_baseline_updated_embed(ticker, threshold, reason, baseline_data, old_baseline)
    return _create_normal_embed(notification_type, ticker, etf_data, threshold, reason,
                                baseline_data=baseline_data, comparison_data=comparison_data)


def send_discord_notification(embed):
    """Discord Webhookで通知を送信"""
    # 米国版と同じWebhook URL環境変数を使用
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

    if not webhook_url:
        print("⚠️ DISCORD_WEBHOOK_URL が設定されていません")
        return False

    payload = {"embeds": [embed]}

    try:
        response = requests.post(webhook_url, json=payload)
        response.raise_for_status()
        print("✅ Discord通知送信成功")
        return True
    except Exception as e:
        print(f"❌ Discord通知送信失敗: {e}")
        return False


def process_ticker(ticker, config, state, today, today_str, current_year):
    """1銘柄分の監視処理。state を直接変更する。"""
    print(f"--- {ticker} ({config['name']}) ---")

    # ETFデータ取得（TTM方式・リトライあり）
    etf_data = _with_retry(get_etf_data, ticker)
    if not etf_data:
        print(f"⚠️ {ticker} のデータ取得失敗\n")

        is_weekend = today.weekday() >= 5

        # 土曜日リマインダーチェック（前回保存データを使用）
        if ticker in state:
            prev = state[ticker]
            should_remind, days_above = _check_saturday_reminder(prev, today)
            if should_remind:
                comparison_data = _build_comparison_data(prev)
                remind_embed = create_discord_embed(
                    "reminder", ticker, _etf_data_from_state(prev),
                    prev.get("threshold", 0),
                    f"週次リマインダー（土曜日、継続{days_above}日目）※前営業日データ",
                    comparison_data=comparison_data
                )
                send_discord_notification(remind_embed)
                state[ticker]["last_reminded"]           = today_str
                state[ticker]["last_reminded_yield"]     = prev.get("current_yield")
                state[ticker]["last_reminded_price_jpy"] = prev.get("price_jpy")
                print(f"   📌 土曜日リマインダー送信（前回データ使用）")

        # 土日はデータ取得失敗通知を送らない（市場休場のため想定内）
        if not is_weekend:
            error_embed = create_discord_embed(
                "error_etf_data",
                ticker,
                None,
                0,
                f"{ETFS[ticker]['name']} のデータ取得に失敗しました。yfinance APIの問題、またはティッカーシンボルの変更が考えられます。この銘柄の監視をスキップします。"
            )
            send_discord_notification(error_embed)
        return

    current_yield = etf_data["yield"]
    last_trade_date = etf_data.get("last_trade_date")

    # 年度更新チェック（baselineの自動更新）
    baseline_update_success = False
    new_baseline = None
    should_update, last_year, is_initial = should_update_baseline(ticker, state, config)
    if should_update:
        new_baseline, baseline_errors = update_baseline(ticker, last_year, state, config, is_initial)

        # baseline更新エラーを通知
        for err in baseline_errors:
            embed = create_discord_embed(
                "error_baseline", ticker, None, 0,
                err["reason"], baseline_data=err["baseline_data"]
            )
            send_discord_notification(embed)

        if new_baseline:
            # baselineを即座に反映
            if ticker not in state:
                state[ticker] = {}
            state[ticker]["baseline"] = {
                "years": new_baseline["years"],
                "yield": new_baseline["yield"]
            }
            # last_yearを今年に更新（年度更新の重複を防ぐ）
            state[ticker]["last_year"] = current_year
            baseline_update_success = True

    # 閾値を取得（更新されたbaselineを使用）
    threshold_data = get_current_threshold(ticker, config, state)
    threshold = threshold_data["threshold"]

    print(f"配当利回り: {current_yield}% (TTM方式)")
    print(f"閾値: {threshold}% (Baseline: {threshold_data['baseline_yield']}%, {threshold_data['baseline_years']}年)")
    print(f"価格: ¥{etf_data['price_jpy']:,.0f}")

    # Baseline更新成功の通知（初回起動の欠落補完を含む）
    if baseline_update_success:
        if is_initial:
            update_message = f"初回起動時に {last_year}年以降のデータ欠落を検知し、自動補完してBaselineを更新しました。"
        else:
            update_message = f"{new_baseline['last_year']}年実績 {new_baseline['last_year_avg']:.2f}% を反映してBaselineを更新しました。"

        update_embed = create_discord_embed(
            "baseline_updated",
            ticker,
            etf_data,
            threshold,
            update_message,
            baseline_data={
                "years": new_baseline["years"],
                "yield": new_baseline["yield"]
            },
            old_baseline=new_baseline["old_baseline"]
        )
        send_discord_notification(update_embed)

    # 通知判定
    should_send, notification_type, reason = should_notify(
        ticker, current_yield, threshold, state, etf_data
    )

    print(f"判定: {reason}")

    # 取引日なしの場合はstate更新をスキップ
    if notification_type == "no_trade":
        print()
        return

    # 初回起動の通知
    if notification_type in ["initial", "initial_above"]:
        initial_embed = create_discord_embed(
            notification_type,
            ticker,
            etf_data,
            threshold,
            reason if notification_type == "initial_above" else "初回起動。この閾値で監視を開始します。",
            baseline_data={
                "years": threshold_data["baseline_years"],
                "yield": threshold_data["baseline_yield"]
            }
        )
        send_discord_notification(initial_embed)
    elif should_send:
        # 通常の通知（上抜け・下抜け・リマインダー）
        comparison_data = None
        if notification_type == "reminder":
            comparison_data = _build_comparison_data(state.get(ticker, {}))
        embed = create_discord_embed(
            notification_type, ticker, etf_data,
            threshold, reason, comparison_data=comparison_data
        )
        send_discord_notification(embed)

    # 状態更新
    new_status = "above" if current_yield >= threshold else "below"

    # 状態オブジェクト作成
    new_state = {
        "status": new_status,
        "current_yield": current_yield,
        "price_jpy": etf_data["price_jpy"],
        "dividend_jpy": etf_data["dividend_jpy"],
        "threshold": threshold,
        "last_trade_date": last_trade_date,
        "last_year": current_year,
        "baseline": {
            "years": threshold_data["baseline_years"],
            "yield": threshold_data["baseline_yield"],
        },
        "last_checked": today_str,
    }

    # 前回の状態を引き継ぐ
    if ticker in state:
        prev_state = state[ticker]
        new_state["last_notified"]           = prev_state.get("last_notified")
        new_state["last_reminded"]           = prev_state.get("last_reminded")
        new_state["crossed_above_date"]      = prev_state.get("crossed_above_date")
        new_state["crossed_above_yield"]     = prev_state.get("crossed_above_yield")
        new_state["crossed_above_price_jpy"] = prev_state.get("crossed_above_price_jpy")
        new_state["last_reminded_yield"]     = prev_state.get("last_reminded_yield")
        new_state["last_reminded_price_jpy"] = prev_state.get("last_reminded_price_jpy")

    # 通知を送った場合の更新（初回起動も含む）
    if should_send:
        new_state["last_notified"] = today_str

        if notification_type in ("crossed_above", "initial_above"):
            new_state["crossed_above_date"]      = today_str
            new_state["crossed_above_yield"]     = current_yield
            new_state["crossed_above_price_jpy"] = etf_data["price_jpy"]
            new_state["last_reminded"]           = today_str
            new_state["last_reminded_yield"]     = current_yield
            new_state["last_reminded_price_jpy"] = etf_data["price_jpy"]
        elif notification_type == "reminder":
            new_state["last_reminded"]           = today_str
            new_state["last_reminded_yield"]     = current_yield
            new_state["last_reminded_price_jpy"] = etf_data["price_jpy"]
        elif notification_type == "crossed_below":
            new_state["crossed_above_date"]      = None
            new_state["crossed_above_yield"]     = None
            new_state["crossed_above_price_jpy"] = None
            new_state["last_reminded"]           = None
            new_state["last_reminded_yield"]     = None
            new_state["last_reminded_price_jpy"] = None

    state[ticker] = new_state
    print()


def main():
    """メイン処理"""
    now_jst = datetime.now(JST)
    today = now_jst.date()
    today_str = today.isoformat()
    current_year = now_jst.year

    print(f"=== ETF利回り監視開始 (JP): {now_jst.strftime('%Y-%m-%d %H:%M:%S JST')} ===\n")

    # 状態ファイル読み込み
    state = load_state()

    # 各ETFを監視
    for ticker, config in ETFS.items():
        process_ticker(ticker, config, state, today, today_str, current_year)

    # 状態保存
    save_state(state)
    print("=== 監視完了 (JP) ===")


if __name__ == "__main__":
    main()
