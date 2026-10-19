# ニュース一覧（自分専用ニュースまとめ）

ヤフトピのような一覧性で、煽り見出し・ゴシップ・広告を除いたニュースまとめページです。

## 仕組み

```
GitHub Actions（30分ごと）
  └ scripts/build_news.py が RSS を取得
      → NG媒体・NGワードで除外（config.toml）
      → 同じ出来事の記事を1つにまとめる
      → site/data/news.json に保存
  └ site/ を GitHub Pages で公開
スマホ・PCのブラウザ
  └ news.json を読み込んで表示、端末ごとのNG設定・既読を適用
```

## ファイル

| ファイル | 内容 |
|---|---|
| `config.toml` | 取得するRSS・ジャンル・共通NGワード/NG媒体（**公開されます**） |
| `scripts/build_news.py` | RSS取得・フィルタ・重複まとめ |
| `site/` | 公開されるページ（index.html / style.css / app.js） |
| `.github/workflows/update.yml` | 30分ごとの自動更新と公開 |
| `.github/workflows/keepalive.yml` | 自動更新が60日で止まらないようにする仕組み |
| `preview.bat` | パソコンで試しに表示する（ダブルクリック） |

## パソコンで試す

`preview.bat` をダブルクリック → ブラウザで http://localhost:8000/ が開きます。
終了は黒い画面を閉じるだけです。

## 公開手順（初回のみ）

1. **GitHubアカウントを作る**：https://github.com/signup
2. **リポジトリを作る**：右上「＋」→「New repository」
   - Repository name：好きな名前（例 `my-news`）。URLの一部になります
   - **Public** を選択（無料プランで Pages を使うため）
   - 「Create repository」
3. **ファイルをアップロード**：作成直後の画面の「uploading an existing file」をクリックし、
   このフォルダの中身（`.github` `scripts` `site` `config.toml` `README.md` `.gitignore`）を
   まとめてドラッグ＆ドロップ →「Commit changes」
   - `site/data` フォルダはアップロード不要です（自動で作られます）
4. **Pages を有効にする**：リポジトリの「Settings」→ 左の「Pages」→
   「Build and deployment」の Source を **GitHub Actions** に変更
5. **初回実行**：「Actions」タブ →「ニュース更新」→「Run workflow」
   - 緑のチェックが付けば完成です
6. **ページを開く**：`https://＜ユーザー名＞.github.io/＜リポジトリ名＞/`
7. **iPhoneのホーム画面に追加**（おすすめ）：Safariでページを開き、
   共有ボタン（□に↑）→「ホーム画面に追加」→「追加」
   - ホーム画面のアイコンから開くと、記事はページの上に重なって表示され、
     「完了」を押すだけで一覧の同じ位置に戻れます

## 日々の使い方

- 一番上の「いまの話題」：全ジャンルから、多くの媒体が報じた話題などを自動で8本選んでいます
- 見出しの右の ▮▮▮：その話題を報じた媒体の数（多いほど大きなニュース）
- 見出しを押すと、その場で概要・写真・他媒体の記事・「記事を読む」ボタンが開きます
- オレンジの●：前回開いたとき以降の新しい記事
- 読んだ記事は灰色になります（設定で「既読を隠す」も可）
- 右上の設定：この端末だけのNGワード・NG媒体（人に見られません）
  - 「スマホとPCで設定を揃える」で書き出し→読み込みすると同じ設定にできます

## 共通設定を変えるには

GitHub上で `config.toml` を開き、鉛筆アイコン（Edit）で編集 →「Commit changes」。
数分後にページへ反映されます。

## 注意

- `config.toml` とリポジトリの中身は誰でも見られます（ページ自体は検索エンジンに載らない設定です）。
- RSSが取れなくなったときは、設定画面の「取得状況」に × が表示されます。
- 定期実行は GitHub 側の混雑で遅れることがあります。
- Googleニュース経由の記事は、元記事のURLを非公式の方法で割り出しています（1回40件まで）。
  Google側の仕様変更で使えなくなった場合は、自動的にGoogle経由のリンクのまま表示されます。
