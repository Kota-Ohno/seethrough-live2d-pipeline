#!/usr/bin/env python3
"""輪郭仕上げ: 全レイヤーのアルファを原本立ち絵のソフトマットで制限する。

原本(白背景)から「白からの距離」でソフトマットを作り、
図形コア(収縮済み)の外側では layer_alpha = min(layer_alpha, matte) にする。
→ シルエット外周の半透明ハロー/ダストが消え、輪郭のAAが原本と一致する。
図形内部(コア)は無変更なので、インペイント済みの隠れパーツに影響しない。

usage: refine_edges.py <in.psd> <original_crop.png> <out.psd>
       --scale S --tx TX --ty TY --canvas C [--ramp 48]
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
ap.add_argument('--ramp', type=int, default=48, help='白からの距離がこの値でマット=1.0')
args = ap.parse_args()
F = TARGET / args.canvas
s4 = args.scale * F

orig = np.asarray(Image.open(args.orig).convert('RGB'), np.int16)
dist_white = (255 - orig).max(axis=2)          # 白からの距離(最大チャンネル)
matte0 = np.clip(dist_white.astype(np.float32) / args.ramp, 0, 1)
core0 = dist_white > 10                        # 図形コア(白でない画素)

oh, ow = orig.shape[:2]
size4 = (round(ow*s4), round(oh*s4))
matte4 = np.asarray(Image.fromarray((matte0*255).astype(np.uint8)).resize(size4, Image.BILINEAR), np.float32)/255
core4 = np.asarray(Image.fromarray(core0).resize(size4, Image.NEAREST))
X, Y = round(args.tx*F), round(args.ty*F)
matte = np.zeros((TARGET, TARGET), np.float32)
core = np.zeros((TARGET, TARGET), bool)
dx0, dy0 = max(0, X), max(0, Y)
dx1, dy1 = min(TARGET, X+size4[0]), min(TARGET, Y+size4[1])
matte[dy0:dy1, dx0:dx1] = matte4[dy0-Y:dy1-Y, dx0-X:dx1-X]
core[dy0:dy1, dx0:dx1] = core4[dy0-Y:dy1-Y, dx0-X:dx1-X]
core = ndimage.binary_erosion(core, iterations=3)  # 内側に3px引いた安全圏

psd = PSDImage.open(args.psd)
outs = []
for l in psd:
    rgba = np.asarray(l.composite().convert('RGBA')).copy()
    x0, y0 = l.bbox[0], l.bbox[1]
    h, w = rgba.shape[:2]
    m = matte[y0:y0+h, x0:x0+w]
    c = core[y0:y0+h, x0:x0+w]
    a = rgba[..., 3].astype(np.float32)
    limited = np.minimum(a, m*255)
    newa = np.where(c, a, limited)
    removed = int((a - newa).sum()/255)
    rgba[..., 3] = newa.astype(np.uint8)
    if removed:
        print(f'{l.name.rstrip(chr(0))}: -{removed}px(相当)')
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
    cle = l.name.rstrip('\x00')
    l._record.name = cle
    if Tag.UNICODE_LAYER_NAME in l._record.tagged_blocks:
        l._record.tagged_blocks.set_data(Tag.UNICODE_LAYER_NAME, cle)
fin._record.image_data = PSDImage.frompil(
    _flat_white(fin))._record.image_data
fin.save(args.out)
print('written:', args.out)
