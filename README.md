# seethrough-live2d-pipeline

AI生成イラスト1枚から、Live2D Cubismで読み込める高解像度パーツ分けPSDを作るためのスクリプト集です。
[ComfyUI-See-through](https://github.com/jtydhr88/ComfyUI-See-through) の自動レイヤー分解出力を、実際にリギングに使える品質へ引き上げます。

**Scripts to turn a single AI-generated anime illustration into a rig-ready, Live2D Cubism-compatible layered PSD, by post-processing [See-through](https://github.com/shitagaki-lab/see-through) decomposition output.** (Docs are in Japanese; the code comments explain each step.)

解説記事: (Zenn記事URL、公開後に差し替え)

## パイプライン

```
立ち絵PNG(白背景)
  → See-through(ComfyUI)でレイヤー分解PSD          … このリポジトリの対象外(RunComfy等で実行)
  → scripts/reproject.py       可視部を原本画素で再投影+Live2D標準レイヤー順に組み替え+4096px化
  → scripts/refine_edges.py    原本のソフトマットで輪郭アルファを置換(ハロー除去)
  → scripts/clean_face.py      faceレイヤーの細線・シミ除去(閉じ目で露出する領域)
  → scripts/agpsd_build.mjs    Cubismが確実に読めるPSDを書き出し(ag-psd)
```

補助スクリプト: `clip_to_silhouette.py`(シルエット外ダストの一括除去)、`restore_accessories.py`(再生成で消えたパーツの差分検出・復元)。

## なぜag-psdで書き出すのか

Pythonの pytoshop で書いたPSDは、Cubism Editorで「パーツ名は認識されるが何も表示されない」状態になります。動くPSD(ag-psd製)との構造diffの結果、header channels=4(合成画像に透明チャンネル)/ layer_count が負値 / global layer mask info の有無が分水嶺でした。詳細は解説記事を参照してください。

## 動作要件

- Python(3.14で動作確認)— `pip install -r requirements.txt`
- Node.js(v26で動作確認)— `npm install ag-psd pngjs`
- [Real-ESRGAN ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN/releases)(`RESRGAN_DIR` 環境変数でディレクトリを指定)

## 使い方の例

```bash
# 1. 再投影(See-through出力PSD + 原本 + 原本の4xアップスケール)
RESRGAN_DIR=~/tools/realesrgan python scripts/reproject.py \
  seethrough_out.psd original.png original_4x.png out-hd.psd --workdir work/

# 2. 輪郭仕上げ(scale/tx/ty/canvasは reproject.py のalignment出力値)
python scripts/refine_edges.py out-hd.psd original.png out-final.psd \
  --scale 1.15488 --tx 674 --ty -1 --canvas 2048

# 3. Cubism互換PSDに書き出し(レイヤーPNG+meta.jsonを用意して)
node scripts/agpsd_build.mjs
```

## 注意

- 生成したモデルの利用は各プラットフォームの規約に従ってください。特に **nizimaはAI生成イラスト由来のLive2D作品の出品・オーダーメイド納品を禁止**しています([nizima AIポリシー](https://docs.nizima.com/guide/ai-policy/)、2026年7月時点)。本スクリプト群は自己利用モデルの制作を想定しています。
- スクリプトは特定の1枚(単一キャラ・白背景・正面立ち絵)で実証したものです。他の絵ではパラメータ調整が必要な場合があります。

## License

MIT
