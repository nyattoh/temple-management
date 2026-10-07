# ローカル日本語フォント

原本は Noto Serif CJK JP Regular Version 2.003 の `NotoSerifCJKjp-Regular.otf`。
原本はローカルのビルド入力として保持し、HTTP 配信・OSS 配布には含めない。
配布物は `fonts/*.woff2`、`noto-serif-subsets.css`、`font-subsets.json` と
SIL Open Font License 1.1 の `OFL.txt`。フォントのライセンスはアプリのライセンスと区別する。

## 分割方法

`scripts/build_fonts.py` は公開された `web/index.html`、`web/app.js`、
`web/style.css` と原本フォントだけを読む。個人名、戒名、データベース、私用ファイルは読まない。
公開コードの文字と ASCII 表示文字を共通 WOFF2 にまとめ、残りの全 Unicode 文字を
1,024 コードポイント境界で分割する。公開コードにない文字を削除する方式ではない。
CSS の `unicode-range` により、表示に必要なブロックだけを同一オリジンから取得する。
ファイル名に内容 SHA-256 の先頭 12 桁を含め、内容更新時のキャッシュを区別する。

共通フォントは静的 UI 用の転送最適化であり、原本の全文字対応を限定しない。
公開コードに文字を追加した場合も、該当する追加ブロックで表示できる。
再ビルドすると新しい公開コードの文字も共通フォントへ含める。
入力テキスト次第で多数のブロックを取得するため、共通フォントのサイズを
全ユーザー・全画面の転送量と同一視しない。

## 今回の生成結果

| 対象 | バイト数 |
| --- | ---: |
| 原本 OTF | 24,573,864 |
| 共通 WOFF2（465 字） | 265,660 |
| WOFF2 全 110 ファイル合計 | 20,208,440 |
| 生成 CSS | 49,683 |
| manifest JSON | 54,836 |

WOFF2 全体は原本 OTF より 17.76% 小さい。
共通 WOFF2 だけならフォント本体の転送量は原本の約 1.08% となる。
生成時の公開コードにある原本収録文字は、共通 WOFF2 の 1 ファイルで対応した。
これは公開コード文字に基づく静的検査であり、ブラウザーでの実測値ではない。
全 Unicode 44,777 字、異体字シーケンス 14,787 組の原本対応を保持した。

## 検証と制約

ビルドは生成 WOFF2 を読み戻して、原本と次を照合する。不一致があれば停止する。

- 全 Unicode cmap の文字と対応グリフの集合。
- cmap format 14 の全異体字シーケンスと対応グリフの集合。
- 各ブロックに残るグリフの `vert`・`vrt2` の置換対応。
- 全ブロックの縦書きメトリクス `vhea`・`vmtx` の存在。

FontTools の全 OpenType レイアウト機能の保持・グリフ閉包を使用する。
検証はフォントテーブルの整合性であり、実ブラウザー・プリンターでの見え方は別途確認する。
ブロックをまたぐ複数文字の OpenType 置換は、ブラウザーのフォント選択により
原本単体と同じシェーピングになるとは限らない。
原本にない外字・異体字はこの変換でも追加できない。必要な場合は別のフォントや
外字資産の確認が必要であり、全文字対応は原本の収録範囲を指す。

## 再生成

ビルド時だけ Python、FontTools、Brotli を使う。アプリの実行時依存には追加しない。
検証したビルド環境は FontTools 4.61.1、Brotli 1.2.0。

原本を同梱していない clone では、[Noto の公式配布ファイル](https://github.com/notofonts/noto-cjk/blob/main/Serif/OTF/Japanese/NotoSerifCJKjp-Regular.otf)
をダウンロードし、`web/assets/NotoSerifCJKjp-Regular.otf` に配置する。
この URL の `main` は更新され得る。今回の原本のバージョンと SHA-256 は
本書の冒頭と `font-subsets.json` で確認する。

```powershell
python -m pip install fonttools==4.61.1 brotli==1.2.0
python scripts/build_fonts.py
```

スクリプトは作業ディレクトリに依存せず、全生成・検証の成功後に CSS と manifest を更新する。
`fonts/` 内の、このスクリプトが付ける名前の古い WOFF2 だけを除く。
CSS・manifest の直接編集はせず、原本を置いてスクリプトを再実行する。
各ファイルのバイト数、SHA-256、対応範囲と検証結果は `font-subsets.json` に記録する。
