108 STUDIO v0.5.1 — Slack確定企画スレッド検出修正

- 最終確定企画の表記ゆれを許容
- 親投稿のthread_tsが自分自身を指す場合も検出
- 日付・返信件数を表示
- 未検出時に検索対象投稿数・一致数を表示
- 認証・PDF・編集UIは変更なし

# 108 STUDIO v0.5.0 — Slack連携

## 導入
既存のファイルをGitHubに上書きしてRenderで再デプロイ。既存のGoogleログイン設定は維持。

Render環境変数に `STUDIO_SLACK_BOT_TOKEN` を追加してください（`xoxb-` で始まるBot User OAuth Token）。**トークンをGitHubにコミットしないでください。**

Slack Appをワークスペースにインストールし、Bot Token Scopesとして `channels:read`, `channels:history`, `groups:read`, `groups:history` を付与して再インストール。対象の公開/非公開チャンネルにBotを招待（`/invite @アプリ名`）。

## 操作
1. クライアント設定を開き「Slackチャンネルを再取得」→ チャンネルを選択 → 「この設定を保存」。
2. 一括取り込みタブの「確定企画スレッドを探す」で `【最終確定企画｜10/28】` 形式の親メッセージを検索。
3. スレッドを選択→「返信を確認する」→採用企画をチェック→「チェックした企画を取り込む」。

## 注意
- Slackのデータはサーバー側から読み取り専用で取得。Slackへの書き込みはしません。
- クライアントとチャンネルの紐付けは従来どおりブラウザローカル保存です。
- Slackの返信1件を企画として抽出。未採用・FB・人選理由は資料に入れません。
- 取り込み後は元の資料ページ構成を再生成します。編集済み資料への追加は慎重に行ってください。
- 取得できる履歴は直近最大1000件程度のチャンネルメッセージ。古いスレッドは検索できない場合があります。
- Slack Appのプランや権限により履歴取得に制限がある場合があります。


## v0.5.3
- PDFページ番号を削除。
- 全ページ右下に108ロゴのみを控えめに表示（幅30px）。
- その他の編集、Slack、PDF出力機能は変更なし。


## v0.5.4 footer correction
- No page numbers in proposal pages, cover, or dividers; small 108 logo remains at lower right.
- Removes legacy page-number footer elements in the rendered preview and print.
- Suppresses CSS print margin-box headers/footers in browsers that support the feature.
- If a browser still adds its own print page number, disable browser print headers/footers; applications cannot override all browser settings.
