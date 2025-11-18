# **GitHub Actions データ監視Bot**

指定したデータソース（API、ウェブサイト、ライブラリなど）からデータを定期的に取得し、設定した閾値に基づいてDiscordへ通知を送信するBotのテンプレートです。

## **概要**

このプロジェクトは、GitHub Actionsを使用して監視スクリプトを毎日（あるいは指定した間隔で）定時に実行します。

* **監視スクリプト**: src/monitor.py  
  * データの取得、閾値の判定、通知の送信ロジックを記述します。  
* **設定ファイル**: src/config.py  
  * 監視対象のアイテム（APIエンドポイント、ティッカーシンボルなど）や、通知の閾値を定義します。  
* **状態ファイル**: data/state.json  
  * 前回の実行時の状態（ステータス、取得した値など）をJSON形式で保存します。

## **主な機能**

* **定時自動実行**: GitHub Actionsのschedule（cron）機能により、設定した日時に自動でスクリプトを実行します。  
* **状態の永続化**: 実行結果（例: status: "above"）をリポジトリ内のJSONファイル（data/state.json）にコミット＆プッシュすることで、前回の状態を記憶します。  
* **閾値ベースの通知**: config.pyで定義した閾値と、取得した現在の値を比較します。  
* **多彩なDiscord通知**:  
  * 監視開始: Botの初回起動時。  
  * 閾値上抜け: 現在の値が閾値を超えた時。  
  * 閾値下抜け: 現在の値が閾値を下回った時。  
  * 週次リマインダー: 閾値を超過した状態が続く場合、指定した曜日（例: 土曜日）にリマインド通知。  
  * エラー通知: データ取得失敗時。  
* **カスタマイズ可能**: src/monitor.py内のデータ取得ロジック（例: get\_data関数）を書き換えるだけで、株式（yfinance）、暗号資産API、気象APIなど、様々な監視に対応可能です。

## **ディレクトリ構成**

.  
├── .github/workflows/  
│   └── monitor.yml           \# GitHub Actions ワークフロー  
├── data/  
│   └── state.json            \# (自動生成) 状態保存ファイル  
├── src/  
│   ├── monitor.py            \# メインの監視スクリプト  
│   └── config.py             \# 設定ファイル  
├── requirements.txt          \# 依存ライブラリ (例: requests, yfinance)  
└── README.md                 \# このファイル

## **セットアップ方法**

1. **リポジトリのフォーク（Fork）**  
   * このリポジトリをご自身のGitHubアカウントにフォークします。  
2. **スクリプトのカスタマイズ**  
   * src/monitor.py: get\_data関数など、監視したいデータを取得するロジックを実装します。（例: requestsでAPIを叩く、yfinanceで株価を取得する）  
   * src/config.py: TARGETSディクショナリに、監視したい対象と閾値（threshold）を定義します。  
   * requirements.txt: monitor.pyで必要なライブラリ（例: requests）を追加します。  
3. **Discord Webhook URLの取得**  
   * 通知を送りたいDiscordサーバーのチャンネルで、「チャンネルの編集」→「連携サービス」→「ウェブフック」→「新しいウェブフック」を作成します。  
   * 作成したWebhookの「Webhook URLをコピー」します。  
4. **GitHub Actions Secretの設定**  
   * フォークしたご自身のGitHubリポジトリで、Settings \> Secrets and variables \> Actions に移動します。  
   * New repository secret をクリックします。  
   * **Name**: DISCORD\_WEBHOOK\_URL  
   * **Value**: (コピーしたWebhook URL)  
   * Add secret をクリックして保存します。

## **実行**

セットアップが完了すると、.github/workflows/monitor.yml ファイルに定義された schedule (cron) に基づいて、GitHub Actionsが自動的にスクリプトを実行します。

（実行時間は monitor.yml の cron 設定を編集することで変更可能です）

また、Actionsタブからワークフローを選択し、Run workflow から手動で実行することも可能です。

## **仕組み（ロジック詳細）**

### **状態保存**

Botは実行のたびに、最新のステータス（例: above / below）を data/state.json に記録します。  
GitHub Actionsは、実行後に変更された state.json を自動的にリポジトリにコミット＆プッシュします。これにより、Botは「前回の状態」を記憶し、状態が変化した時（例: below \-\> above）にのみ通知を送ることができます。