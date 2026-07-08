import { writePsdBuffer } from 'ag-psd';
import { PNG } from 'pngjs';
import fs from 'fs';

const meta = JSON.parse(fs.readFileSync('meta.json', 'utf8'));

function loadPng(path) {
  const png = PNG.sync.read(fs.readFileSync(path));
  return { width: png.width, height: png.height, data: new Uint8ClampedArray(png.data) };
}

// ag-psd: childrenは配列先頭が「下」のレイヤー
const children = meta.layers.map(m => ({
  name: m.name,
  left: m.left,
  top: m.top,
  opacity: 1,
  blendMode: 'normal',
  imageData: loadPng(`layers/${String(m.i).padStart(2, '0')}.png`),
}));

const psd = {
  width: meta.width,
  height: meta.height,
  children,
  imageData: loadPng('layers/_merged.png'),  // 合成プレビュー(透明チャンネル付き)
};

const buf = writePsdBuffer(psd, { generateThumbnail: false });
fs.writeFileSync('out.psd', buf);
console.log('written out.psd', (buf.length / 1024 / 1024).toFixed(1), 'MB');
