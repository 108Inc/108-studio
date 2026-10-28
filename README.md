108 STUDIO v0.5.20
AI field editing now opens a side-by-side original / AI suggestion comparison with an editable final draft. Nothing is applied until explicitly confirmed.


v0.5.22: 資料内のタイトル・企画内容・撮影・狙い・補足・自由記入欄をクリックするとAI編集ボタンを表示。既存の比較・採用フローに接続。

v0.5.23: AI文章編集は短縮一辺倒を廃止。原文の情報の範囲内で短文を自然に補い、長文は圧縮。読みやすい文・行数を優先。比較・採用は維持。

## v0.5.24
- 企画プレビューの余白・背景をダブルクリックすると該当企画の編集画面を開きます。
- 文字の直接編集、画像操作、リンククリックは従来どおりです。


v0.5.26: Slack import rendering guard, pageOrder reconciliation and visible page count verification.


v0.5.27: Fix workspace ReferenceError on idea page render (var function scope); preserve existing imported ideas.


## v0.5.28
- 編集タブ左側をSlack原文・画像位置調整に限定
- クライアント設定は従来の左パネルを継続
- 企画本文・タイトル・AI編集はプレビュー上の操作を維持
- 重複する編集フォーム・ナビゲーション・サムネイル一括操作を編集タブで非表示
- デスクトップ時の左右比率を約25:75に変更


v0.5.29: PC editing view uses independent scrolling in sidebar and page preview, keeping the page visible while adjusting sidebar controls. Print and mobile layouts unchanged.


v0.5.30: 資料内AIボタンを左側の編集フォームから独立させ、直接AI比較画面を開くよう修正。


v0.5.31: A4 print page break and blank free-workspace correction.


v0.5.32: Slack取り込みをテキスト/PDF取り込みより上に移動。機能・IDは維持。


## v0.5.33
- PDF保存ボタンからブラウザ印刷ダイアログを開かずにPDFを直接ダウンロードします。
- 初回にCDNから html2pdf.js を読み込むためネット接続が必要です。
- PDFは画像ベースとなるため、文字の選択や参考動画リンクのクリックはできません。印刷方式に戻す場合は前版を使用してください。


v0.5.34: PDF direct download retains clickable reference video links by adding jsPDF URI annotations over the thumbnail and reference link.
