#!/usr/bin/env python3
"""faceレイヤーのノイズ除去。

対象ノイズ: 再投影時に焼き込まれた前髪フリンジの線、AIインペイントの目跡・シミ。
2パス構成:
  pass1: ブラックハット/トップハット変換で「細い暗線・明点」だけ検出し、
         クロージング/オープニングで置換(ぼかさないので紅み・陰影は無傷)。
         シルエット輪郭6pxは保護。
  pass2: 目領域(eyewhite/irides/eyelash のアルファ結合+3px膨張)のみ
         cv2.inpaint(Telea)。
教訓: 前髪の下全域など広域のインペイントは顔の構造ごと消すため厳禁(2回失敗済み)。

usage: clean_face.py <layers_dir> <meta.json>
  layers_dir: agpsd_build.mjs 用のレイヤーPNG群(face を上書きする)
  実行後に `node agpsd_build.mjs` でPSD再構築すること。
"""
import sys, json
import numpy as np
import cv2
from PIL import Image
from scipy import ndimage

layers_dir, meta_path = sys.argv[1], sys.argv[2]
meta = json.load(open(meta_path))
names = {m['name']: m for m in meta['layers']}
fm = names['face']
fx, fy = fm['left'], fm['top']
face = np.asarray(Image.open(f"{layers_dir}/{fm['i']:02d}.png")).copy()
fh, fw = face.shape[:2]

# --- pass1: 細線・点の形態学的除去 ---
rgb = face[..., :3]
L = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
bh = cv2.morphologyEx(L, cv2.MORPH_BLACKHAT, k)
th = cv2.morphologyEx(L, cv2.MORPH_TOPHAT, k)
lines = (bh > 18) | (th > 24)
alpha_in = ndimage.binary_erosion(face[..., 3] > 128, iterations=6)  # 輪郭線保護
lines = ndimage.binary_dilation(lines & alpha_in, iterations=1)
close = cv2.morphologyEx(rgb, cv2.MORPH_CLOSE, k)
openm = cv2.morphologyEx(rgb, cv2.MORPH_OPEN, k)
fill = np.where((bh > 18)[..., None], close, openm)
out = rgb.copy()
out[lines] = fill[lines]
blur = cv2.GaussianBlur(out, (5, 5), 1.2)
seam = ndimage.binary_dilation(lines, iterations=2) & alpha_in
out[seam] = blur[seam]
print(f'pass1 line/speck px: {int(lines.sum())}')

# --- pass2: 目領域のみ局所インペイント ---
mask = np.zeros((fh, fw), bool)
for n in ['eyewhite-l', 'eyewhite-r', 'irides-l', 'irides-r', 'eyelash-l', 'eyelash-r']:
    m = names[n]
    a = np.asarray(Image.open(f"{layers_dir}/{m['i']:02d}.png"))[..., 3]
    ox, oy = m['left'] - fx, m['top'] - fy
    h, w = a.shape
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(fw, ox + w), min(fh, oy + h)
    if x1 > x0 and y1 > y0:
        mask[y0:y1, x0:x1] |= a[y0 - oy:y1 - oy, x0 - ox:x1 - ox] > 8
mask = ndimage.binary_dilation(mask, iterations=3) & (face[..., 3] > 0)
print(f'pass2 eye-region inpaint px: {int(mask.sum())}')
out = cv2.inpaint(out, mask.astype(np.uint8) * 255, 5, cv2.INPAINT_TELEA)

Image.fromarray(np.dstack([out, face[..., 3]])).save(f"{layers_dir}/{fm['i']:02d}.png")
print('face layer updated. 次: node agpsd_build.mjs でPSD再構築')
