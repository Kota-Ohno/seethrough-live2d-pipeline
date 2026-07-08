#!/usr/bin/env python3
"""全レイヤーのアルファを「原本立ち絵のシルエット(膨張付き)」でクリップする。
See-through再生成がシルエット外に出したダスト・エッジ逸脱を一括除去。
インペイント領域(隠れパーツ)はすべて原本シルエット内側なので影響しない。

usage: clip_to_silhouette.py <in.psd> <original_crop.png> <out.psd>
       --scale S --tx TX --ty TY --canvas C [--dilate 3]
"""
import argparse
import numpy as np
from PIL import Image
from psd_tools import PSDImage
from psd_tools.constants import Tag
from pytoshop.user import nested_layers
from pytoshop import enums
from scipy import ndimage

Image.MAX_IMAGE_PIXELS = None
TARGET = 4096

def _flat_white(psd):
    """レイヤーを白背景へ正しくアルファ合成したRGB画像(内蔵プレビュー用)。
    composite()のRGBA出力をconvert('RGB')すると半透明画素に生の色が残るため、
    必ず手動でアルファブレンドする。"""
    import numpy as _np
    from PIL import Image as _Image
    a = _np.asarray(psd.composite(force=True)).astype(_np.float32)
    al = a[..., 3:4] / 255
    return _Image.fromarray((a[..., :3] * al + 255 * (1 - al)).astype(_np.uint8))


ap = argparse.ArgumentParser()
ap.add_argument('psd'); ap.add_argument('orig'); ap.add_argument('out')
ap.add_argument('--scale', type=float, required=True)
ap.add_argument('--tx', type=float, required=True)
ap.add_argument('--ty', type=float, required=True)
ap.add_argument('--canvas', type=int, required=True)
ap.add_argument('--dilate', type=int, default=3)
args = ap.parse_args()
F = TARGET / args.canvas

orig = Image.open(args.orig).convert('RGB')
omask = (np.asarray(orig) < 245).any(axis=2)
s4 = args.scale * F
size4 = (round(orig.width*s4), round(orig.height*s4))
om4 = np.asarray(Image.fromarray(omask).resize(size4, Image.NEAREST))
X, Y = round(args.tx*F), round(args.ty*F)
figmask = np.zeros((TARGET, TARGET), bool)
dx0, dy0 = max(0, X), max(0, Y)
dx1, dy1 = min(TARGET, X+size4[0]), min(TARGET, Y+size4[1])
figmask[dy0:dy1, dx0:dx1] = om4[dy0-Y:dy1-Y, dx0-X:dx1-X]
figmask = ndimage.binary_dilation(figmask, iterations=args.dilate)

psd = PSDImage.open(args.psd)
outs = []
for l in psd:
    rgba = np.asarray(l.composite().convert('RGBA')).copy()
    x0, y0 = l.bbox[0], l.bbox[1]
    h, w = rgba.shape[:2]
    sub = figmask[y0:y0+h, x0:x0+w]
    removed = int(((rgba[..., 3] > 0) & ~sub).sum())
    rgba[~sub] = 0
    if removed:
        print(f'{l.name}: clipped {removed}px')
    outs.append(nested_layers.Image(
        name=l.name.rstrip('\x00'), top=y0, left=x0,
        channels={0: rgba[..., 0], 1: rgba[..., 1], 2: rgba[..., 2], -1: rgba[..., 3]},
        opacity=255, visible=True, blend_mode=enums.BlendMode.normal))
psd_out = nested_layers.nested_layers_to_psd(
    list(reversed(outs)), color_mode=enums.ColorMode.rgb,
    size=(TARGET, TARGET), depth=enums.ColorDepth.depth8,
    compression=enums.Compression.raw)
with open(args.out, 'wb') as f:
    psd_out.write(f)
fin = PSDImage.open(args.out)
for l in fin:
    c = l.name.rstrip('\x00')
    l._record.name = c
    if Tag.UNICODE_LAYER_NAME in l._record.tagged_blocks:
        l._record.tagged_blocks.set_data(Tag.UNICODE_LAYER_NAME, c)
fin._record.image_data = PSDImage.frompil(
    _flat_white(fin))._record.image_data
fin.save(args.out)
print('written:', args.out)
