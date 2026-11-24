/**
 * Pixel comparison of two PNG buffers (same size): share of pixels that differ beyond
 * pixelmatch's perceptual threshold, plus an optional diff image.
 */
import { writeFileSync } from 'node:fs';
import pixelmatch from 'pixelmatch';
import { PNG } from 'pngjs';

export function comparePng(a, b, { threshold = 0.1, diffPath } = {}) {
  const A = PNG.sync.read(a);
  const B = PNG.sync.read(b);
  if (A.width !== B.width || A.height !== B.height)
    return {
      ratio: 1,
      pixels: -1,
      size: `${A.width}x${A.height} vs ${B.width}x${B.height}`,
    };
  const out = new PNG({ width: A.width, height: A.height });
  const pixels = pixelmatch(A.data, B.data, out.data, A.width, A.height, {
    threshold,
    includeAA: false,
    alpha: 0.2,
    diffMask: false,
  });
  if (diffPath) writeFileSync(diffPath, PNG.sync.write(out));
  // Structural differences (a missing icon, a wrong fill) are dense; anti-aliasing noise on text
  // edges is sparse. Count 8x8 blocks where at least 3/8 of the pixels differ.
  let blocks = 0;
  for (let by = 0; by + 8 <= A.height; by += 8) {
    for (let bx = 0; bx + 8 <= A.width; bx += 8) {
      let n = 0;
      for (let y = by; y < by + 8; y++) {
        for (let x = bx; x < bx + 8; x++) {
          const i = (y * A.width + x) * 4;
          if (
            out.data[i] === 255 &&
            out.data[i + 1] === 0 &&
            out.data[i + 2] === 0
          )
            n++;
        }
      }
      if (n >= 24) blocks++;
    }
  }
  return { ratio: pixels / (A.width * A.height), pixels, blocks };
}
