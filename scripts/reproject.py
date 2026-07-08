#!/usr/bin/env python3
"""See-through出力PSDの仕上げパイプライン。

1. 各レイヤーをReal-ESRGAN(anime)でターゲット解像度へアップスケール
2. 原本立ち絵との座標変換を推定(bboxフィット+IoUグリッド精密化)
3. 可視画素の「持ち主」をSee-throughの元レイヤー順(=原画に忠実な順)で判定し、
   可視部のRGBを原本(4xアップスケール済み)から移植。隠れ部分のみAI生成画素を残す
4. レイヤー別アルファ・デスペックル(孤立小成分の除去)
5. レイヤー順をLive2D標準スタッキングに組み替え
6. pytoshopでPSD書き出し → レイヤー名NUL除去 → 合成プレビュー埋め込み

usage: reproject.py <seethrough.psd> <original_crop.png> <original_crop_4x.png> <out.psd> [--workdir DIR]
"""
import argparse, json, os, subprocess, sys
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

RESRGAN = os.environ.get('RESRGAN_DIR')  # realesrgan-ncnn-vulkan のディレクトリ

# Live2D標準スタッキング(下→上)。See-through v0.0.2の命名に対応
LIVE2D_ORDER = [
    'back hair', 'neck', 'legwear', 'footwear', 'bottomwear', 'topwear',
    'handwear-l', 'handwear-r', 'face', 'ears-l', 'ears-r',
    'eyewhite-l', 'eyewhite-r', 'irides-l', 'irides-r',
    'eyelash-l', 'eyelash-r', 'eyebrow-l', 'eyebrow-r',
    'nose', 'mouth', 'front hair', 'headwear',
]

def upscale(src, dst, scale_to):
    """Real-ESRGAN 4x → 必要サイズへLanczos縮小(dst既存ならスキップ)"""
    if os.path.exists(dst):
        return Image.open(dst).size
    tmp = dst + '.4x.png'
    subprocess.run([f'{RESRGAN}/realesrgan-ncnn-vulkan', '-i', src, '-o', tmp,
                    '-n', 'realesrgan-x4plus-anime', '-s', '4', '-m', f'{RESRGAN}/models'],
                   check=True, capture_output=True)
    im = Image.open(tmp)
    if scale_to != 1.0:
        im = im.resize((round(im.width*scale_to), round(im.height*scale_to)), Image.LANCZOS)
    im.save(dst); os.remove(tmp)
    return im.size

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('psd'); ap.add_argument('orig'); ap.add_argument('orig4x')
    ap.add_argument('out'); ap.add_argument('--workdir', default='reproject_work')
    args = ap.parse_args()
    os.makedirs(args.workdir, exist_ok=True)

    psd = PSDImage.open(args.psd)
    C = psd.width
    F = TARGET / C
    print(f'canvas {C} -> {TARGET} (x{F:.2f}), {len(list(psd))} layers')

    # --- 1. レイヤー抽出 + アップスケール ---
    meta = []
    for i, layer in enumerate(psd):
        img = layer.composite().convert('RGBA')
        src = f'{args.workdir}/{i:02d}.png'
        img.save(src)
        dst = f'{args.workdir}/{i:02d}_up.png'
        upscale(src, dst, scale_to=F/4)
        meta.append({'i': i, 'name': layer.name.rstrip('\x00'), 'bbox': list(layer.bbox)})
        print(f'  up {i:02d} {meta[-1]["name"]}')

    # --- 2. 座標変換推定(canvas座標系) ---
    orig = Image.open(args.orig).convert('RGB')
    omask = (np.asarray(orig) < 245).any(axis=2)
    oys, oxs = np.where(omask)
    union = np.zeros((C, C), bool)
    for m in meta:
        a = np.asarray(Image.open(f'{args.workdir}/{m["i"]:02d}.png'))[..., 3]
        x0, y0, x1, y1 = m['bbox']
        union[y0:y1, x0:x1] |= a > 32
    uys, uxs = np.where(union)
    s0 = ((uxs.max()-uxs.min())/(oxs.max()-oxs.min()) + (uys.max()-uys.min())/(oys.max()-oys.min()))/2
    tx0, ty0 = uxs.min()-oxs.min()*s0, uys.min()-oys.min()*s0
    # IoUグリッド精密化
    best = (0, s0, tx0, ty0)
    for s in np.linspace(s0*0.997, s0*1.003, 7):
        om = np.asarray(Image.fromarray(omask).resize(
            (round(orig.width*s), round(orig.height*s)), Image.NEAREST))
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                tx, ty = round(tx0)+dx-round(oxs.min()*(s-s0)), round(ty0)+dy-round(oys.min()*(s-s0))
                canv = np.zeros((C, C), bool)
                h, w = om.shape
                x0c, y0c = max(0, tx), max(0, ty)
                canv[y0c:min(C, ty+h), x0c:min(C, tx+w)] = om[y0c-ty:min(C, ty+h)-ty, x0c-tx:min(C, tx+w)-tx]
                iou = (canv & union).sum() / (canv | union).sum()
                if iou > best[0]:
                    best = (iou, s, tx, ty)
    iou, s, tx, ty = best
    print(f'alignment: scale={s:.5f} offset=({tx},{ty}) IoU={iou:.4f}')

    # --- 3. 原本ディテールキャンバス(4096) ---
    s4 = s * F
    o4 = Image.open(args.orig4x)  # 原本の4x
    o4 = o4.resize((round(orig.width*s4), round(orig.height*s4)), Image.LANCZOS).convert('RGB')
    detail = np.full((TARGET, TARGET, 3), 255, np.uint8)
    X, Y = round(tx*F), round(ty*F)
    # 負オフセット対応: キャンバス側・ソース側双方をクリップ
    dx0, dy0 = max(0, X), max(0, Y)
    dx1, dy1 = min(TARGET, X+o4.width), min(TARGET, Y+o4.height)
    o4a = np.asarray(o4)
    detail[dy0:dy1, dx0:dx1] = o4a[dy0-Y:dy1-Y, dx0-X:dx1-X]
    figmask = np.zeros((TARGET, TARGET), bool)
    om4 = np.asarray(Image.fromarray(omask).resize(o4.size, Image.NEAREST))
    figmask[dy0:dy1, dx0:dx1] = om4[dy0-Y:dy1-Y, dx0-X:dx1-X]
    figmask = ndimage.binary_dilation(figmask, iterations=4)

    # --- 4+5. デスペックル + 可視部RGB移植(元レイヤー順の上から) ---
    canvases = {}
    for m in meta:
        rgba = np.zeros((TARGET, TARGET, 4), np.uint8)
        up = np.asarray(Image.open(f'{args.workdir}/{m["i"]:02d}_up.png').convert('RGBA'))
        x0, y0 = round(m['bbox'][0]*F), round(m['bbox'][1]*F)
        h, w = up.shape[:2]
        rgba[y0:min(TARGET, y0+h), x0:min(TARGET, x0+w)] = up[:TARGET-y0, :TARGET-x0]
        # デスペックル: 面積<200pxの孤立アルファ成分を除去
        lab, n = ndimage.label(rgba[..., 3] > 16)
        if n > 1:
            sizes = ndimage.sum_labels(np.ones_like(lab), lab, range(1, n+1))
            kill = np.isin(lab, np.where(sizes < 200)[0]+1)
            rgba[kill] = 0
        canvases[m['i']] = rgba
    claimed = np.zeros((TARGET, TARGET), bool)
    for m in reversed(meta):  # psd-tools順: 末尾=最上層 → 上から下へ
        rgba = canvases[m['i']]
        a = rgba[..., 3]
        own = (a > 16) & ~claimed & figmask
        rgba[own, :3] = detail[own]
        # 128: 半透明の上層エッジ(前髪の毛先等)にも所有権を与え、
        # 下層(顔など)に上層の色が移植される汚染を防ぐ
        claimed |= a >= 128
        print(f'  transplant {m["name"]}: {int(own.sum())}px')

    # --- 6. Live2D順に組み替えて書き出し ---
    known = [n for n in LIVE2D_ORDER if n in {m['name'] for m in meta}]
    extra = [m['name'] for m in meta if m['name'] not in LIVE2D_ORDER]
    if extra:
        print('WARN: 未知レイヤーは最上位に配置:', extra)
    order = known + extra  # 下→上
    byname = {m['name']: m for m in meta}
    layers_out = []
    for name in order:
        rgba = canvases[byname[name]['i']]
        ys, xs = np.where(rgba[..., 3] > 0)
        y0, y1, x0, x1 = ys.min(), ys.max()+1, xs.min(), xs.max()+1
        crop = rgba[y0:y1, x0:x1]
        layers_out.append(nested_layers.Image(
            name=name, top=int(y0), left=int(x0),
            channels={0: crop[..., 0], 1: crop[..., 1], 2: crop[..., 2], -1: crop[..., 3]},
            opacity=255, visible=True, blend_mode=enums.BlendMode.normal))
    out_psd = nested_layers.nested_layers_to_psd(
        list(reversed(layers_out)), color_mode=enums.ColorMode.rgb,
        size=(TARGET, TARGET), depth=enums.ColorDepth.depth8,
        compression=enums.Compression.raw)
    with open(args.out, 'wb') as f:
        out_psd.write(f)

    # --- 後処理: 名前NUL除去 + プレビュー埋め込み(psd-tools) ---
    fin = PSDImage.open(args.out)
    for layer in fin:
        clean = layer.name.rstrip('\x00')
        layer._record.name = clean
        if Tag.UNICODE_LAYER_NAME in layer._record.tagged_blocks:
            layer._record.tagged_blocks.set_data(Tag.UNICODE_LAYER_NAME, clean)
    comp = _flat_white(fin)
    fin._record.image_data = PSDImage.frompil(comp)._record.image_data
    fin.save(args.out)
    print('names:', [l.name for l in PSDImage.open(args.out)])
    print('written:', args.out)

if __name__ == '__main__':
    main()
