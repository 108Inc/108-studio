# 108 STUDIO v0.6.0 β — 共同編集テスト版

v0.5.4 を基に、既存の Google ログイン・Slack 取り込み・資料編集・PDF 印刷を維持し、Supabase による共有資料保存を追加しました。

## Render 環境変数

- `STUDIO_GOOGLE_CLIENT_ID`, `STUDIO_ALLOWED_EMAILS`, `STUDIO_SESSION_SECRET`（既存）
- `STUDIO_SLACK_BOT_TOKEN`（既存）
- `SUPABASE_URL`（Supabase の Project URL）
- `SUPABASE_SECRET_KEY`（Supabase の Secret key。絶対に GitHub に登録しない）

## 使用方法

1. GitHub で **テスト用ブランチ** を作り、この ZIP のファイルを上書き。Render で **別のテスト用 Web Service** を作って動作検証してください。既存の本番 URL を直接置き換えないでください。
2. Google OAuth の許可済みアカウントでログインします。テスト用 URL を利用する場合、Google OAuth の Authorized JavaScript origins の追加が必要なことがあります。
3. 既存のローカル資料を開き「＋ 今の資料を共有保存」。同じ URL をもう一人に共有してください。
4. 別々の項目を編集すると、フォーカスが外れたとき約2秒間隔で相手側に反映されます。
5. 「変更履歴」は編集バージョンと編集者の一覧表示のみです。復元 UI は未実装です。

## 安全設計・制約

- Supabase の秘密キーは Render のサーバー側だけで利用。ブラウザには渡しません。
- Google ログインのメール許可リストでアクセス制限。**許可リスト内の全員が共有資料を閲覧・編集できます**。クライアントごとのアクセス権は未実装です。
- `studio_documents` の version を使って競合更新を検知。異なる項目なら再マージを試み、同じ項目の競合なら自動上書きを停止します。
- **Google Docs のような文字単位の同時編集ではありません**。2秒間隔の更新確認を使う準リアルタイム共同編集です。
- 画像は JSON に埋め込んで保存するため、1資料約4MB以下。大量の画像を扱う場合は Supabase Storage に移す必要があります。
- 同時にページを追加・削除・並び替える操作は競合しやすいため、同時操作は避けてください。
- クライアント設定と Slack チャンネルの紐付けは従来どおりブラウザローカルに保存され、共有資料には同期されません。
- 変更履歴は更新時のベストエフォート記録。厳密な監査ログ・履歴復元・編集者のリアルタイム表示・編集ロックは今後の課題です。
- 無料プランでは非アクティブ時の停止やバックアップ制限があります。重要資料は PDF 等でもバックアップしてください。

## データベース

以前に作成した `studio_documents` / `studio_revisions` 等のテーブルを利用します。新しい SQL の実行は不要です。

## 動作確認

- `python -m py_compile server.py cloud_backend.py`
- `node --check` で HTML 内の JavaScript の構文検査
- 本番の Supabase / Google OAuth に接続する E2E テストは未実施です。
