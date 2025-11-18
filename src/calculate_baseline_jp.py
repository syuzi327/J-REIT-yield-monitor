import yfinance as yf
from datetime import datetime

def calculate_average_yield(ticker, start_year, end_year):
    """
    指定された期間の年次利回りの平均を計算する
    年次利回り = (その年の分配金総額) / (その年の年末の株価)
    """
    print(f"--- {ticker} の平均利回り計算開始 ---")
    print(f"対象期間: {start_year}年 ～ {end_year}年")
    
    try:
        etf = yf.Ticker(ticker)
        
        # 最初に全期間の配当データを取得
        all_dividends = etf.dividends
        if all_dividends.empty:
            print(f"❌ エラー: {ticker} の配当データを取得できませんでした。")
            return
            
    except Exception as e:
        print(f"❌ エラー: {ticker} のデータ取得に失敗: {e}")
        return

    annual_yields = []
    
    for year in range(start_year, end_year + 1):
        print(f"\nProcessing {year}年...")
        
        start_date = f"{year}-01-01"
        # 翌年の1月1日まで取得して、その年の最終営業日を見つける
        end_date_for_history = f"{year+1}-01-01" 
        
        try:
            # 履歴データを取得
            history = etf.history(start=start_date, end=end_date_for_history)
            
            if history.empty:
                print(f"  ⚠️ {year}年: 株価履歴データがありません。スキップします。")
                continue
                
            # 年末の株価（その年の最終取引日の終値）
            # end_date_for_history (翌年1月1日) を指定しているので、
            # 取得したデータの最後の行がその年の最終営業日になる
            year_end_price = history["Close"].iloc[-1]
            last_trade_date = history.index[-1].date()
            print(f"  株価: ¥{year_end_price:,.2f} (最終取引日: {last_trade_date})")

            # その年の配当データをフィルタリング
            # end_dateは 'YYYY-12-31' を使用
            end_date_for_dividends = f"{year}-12-31"
            year_dividends = all_dividends[
                (all_dividends.index >= start_date) & 
                (all_dividends.index <= end_date_for_dividends)
            ]
            
            if year_dividends.empty:
                print(f"  ⚠️ {year}年: 分配金データがありません。スキップします。")
                continue

            # 年間分配金総額
            total_dividend = year_dividends.sum()
            print(f"  分配金総額: ¥{total_dividend:,.2f}")

            # 年次利回りを計算
            annual_yield = (total_dividend / year_end_price) * 100
            print(f"  ✅ {year}年 利回り: {annual_yield:.2f}%")
            
            annual_yields.append(annual_yield)

        except Exception as e:
            print(f"  ❌ {year}年: データ処理中にエラーが発生: {e}")
            
    # --- ループ終了 ---
    
    if not annual_yields:
        print("\n--- 計算結果 ---")
        print("有効なデータがありませんでした。")
        return

    # 平均利回りを計算
    average_yield = sum(annual_yields) / len(annual_yields)
    
    print("\n--- 計算結果 ---")
    print(f"対象年数 (データ取得成功年数): {len(annual_yields)}年")
    print(f"平均利回り (2009-2024): {average_yield:.2f}%")
    
    print("\n--- config_jp.py 用の値 ---")
    print(f'"baseline_years": {len(annual_yields)},')
    print(f'"baseline_yield": {average_yield:.2f},')
    print(f'"baseline_year_end": {end_year},')


if __name__ == "__main__":
    TICKER = "1343.T"
    START_YEAR = 2009
    
    # 2025年になったので、2024年のデータまで含める
    END_YEAR = 2024 
    
    calculate_average_yield(TICKER, START_YEAR, END_YEAR)