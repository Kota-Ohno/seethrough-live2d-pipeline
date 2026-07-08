#!/usr/bin/env python3
"""再投影済みPSDと原本の差分から、See-through再生成で消えたパーツ(髪飾り等)を
原本画素の独立レイヤー「accessories」として最前面に復元する。

usage: restore_accessories.py <hd.psd> <original_crop.png> <original_crop_4x.png> <out.psd>
       [--scale S --tx TX --ty TY]  # reproject.pyのalignment出力(canvas座標系)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('psd'); ap.add_argument('orig'); ap.add_argument('orig4x'); ap.add_argument('out')
    ap.add_argument('--scale', type=float, required=True)  # 原本→処理キャンバスのscale
    ap.add_argument('--tx', type=float, required=True)
    ap.add_argument('--ty', type=float, required=True)
    ap.add_argument('--canvas', type=int, required=True)   # 処理キャンバス幅(2048等)
    ap.add_argument('--min-area', type=int, default=800)
    ap.add_argument('--diff-thresh', type=int, default=48)
    args = ap.parse_args()
    F = TARGET / args.canvas

    hd = PSDImage.open(args.psd)
    comp = np.asarray(_flat_white(hd), np.int16)

    # 原本ディテールキャンバス再構築(reproject.pyと同じ変換)
    orig = Image.open(args.orig).convert('RGB')
    omask = (np.asarray(orig) < 245).any(axis=2)
    s4 = args.scale * F
    o4 = Image.open(args.orig4x).resize(
        (round(orig.width*s4), round(orig.height*s4)), Image.LANCZOS).convert('RGB')
    X, Y = round(args.tx*F), round(args.ty*F)
    detail = np.full((TARGET, TARGET, 3), 255, np.uint8)
    figmask = np.zeros((TARGET, TARGET), bool)
    dx0, dy0 = max(0, X), max(0, Y)
    dx1, dy1 = min(TARGET, X+o4.width), min(TARGET, Y+o4.height)
    detail[dy0:dy1, dx0:dx1] = np.asarray(o4)[dy0-Y:dy1-Y, dx0-X:dx1-X]
    om4 = np.asarray(Image.fromarray(omask).resize(o4.size, Image.NEAREST))
    figmask[dy0:dy1, dx0:dx1] = om4[dy0-Y:dy1-Y, dx0-X:dx1-X]

    # 差分: 原本にあって現composite に無い(または大きく違う)領域
    dist = np.abs(comp - detail.astype(np.int16)).sum(axis=2)
    miss = (dist > args.diff_thresh * 3) & figmask
    miss = ndimage.binary_opening(miss, iterations=2)
    lab, n = ndimage.label(miss)
    sizes = ndimage.sum_labels(np.ones_like(lab), lab, range(1, n+1))
    keep = np.isin(lab, np.where(sizes >= args.min_area)[0]+1)
    print(f'diff components: {n}, kept: {int((sizes >= args.min_area).sum())}, px: {int(keep.sum())}')
    # 取りこぼした縁を回収して軽くフェザー
    keep = ndimage.binary_dilation(keep, iterations=3) & figmask
    alpha = ndimage.gaussian_filter(keep.astype(np.float32), 1.2)
    alpha8 = np.clip(alpha*255, 0, 255).astype(np.uint8)

    # デバッグ用マスク画像
    Image.fromarray((keep*255).astype(np.uint8)).save(args.out + '.mask.png')

    # 既存レイヤー+accessoriesで再構築
    layers = []
    for l in hd:
        rgba = np.asarray(l.composite().convert('RGBA'))
        layers.append((l.name.rstrip('\x00'), l.bbox[0], l.bbox[1], rgba))
    ys, xs = np.where(alpha8 > 0)
    y0, y1, x0, x1 = ys.min(), ys.max()+1, xs.min(), xs.max()+1
    acc = np.dstack([detail[y0:y1, x0:x1], alpha8[y0:y1, x0:x1]])
    layers.append(('accessories', x0, y0, acc))
    outs = []
    for name, lx, ly, rgba in layers:
        outs.append(nested_layers.Image(
            name=name, top=int(ly), left=int(lx),
            channels={0: rgba[...,0], 1: rgba[...,1], 2: rgba[...,2], -1: rgba[...,3]},
            opacity=255, visible=True, blend_mode=enums.BlendMode.normal))
    psd_out = nested_layers.nested_layers_to_psd(
        list(reversed(outs)), color_mode=enums.ColorMode.rgb,
        size=(TARGET, TARGET), depth=enums.ColorDepth.depth8,
        compression=enums.Compression.raw)
    with open(args.out, 'wb') as f:
        psd_out.write(f)
    # 名前NUL除去+プレビュー
    fin = PSDImage.open(args.out)
    for l in fin:
        c = l.name.rstrip('\x00')
        l._record.name = c
        if Tag.UNICODE_LAYER_NAME in l._record.tagged_blocks:
            l._record.tagged_blocks.set_data(Tag.UNICODE_LAYER_NAME, c)
    fin._record.image_data = PSDImage.frompil(
        _flat_white(fin))._record.image_data
    fin.save(args.out)
    print('written:', args.out, '| layers:', [l.name for l in PSDImage.open(args.out)][-3:])

if __name__ == '__main__':
    main()
