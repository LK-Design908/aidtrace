// Photo capture: re-encodes through a canvas, which drops ALL metadata (EXIF, GPS, maker notes),
// and compresses to about 100 KB. The original file never leaves this function.
const TARGET_BYTES = 100_000;

export async function compressPhoto(file: File): Promise<{ b64: string; bytes: number }> {
  const bmp = await createImageBitmap(file);   // honours orientation in modern browsers
  let maxSide = 1280;
  for (let attempt = 0; attempt < 6; attempt++) {
    const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const w = Math.round(bmp.width * scale), h = Math.round(bmp.height * scale);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    canvas.getContext("2d")!.drawImage(bmp, 0, 0, w, h);
    for (const q of [0.7, 0.55, 0.4]) {
      const blob: Blob = await new Promise((r) => canvas.toBlob((b) => r(b!), "image/jpeg", q));
      if (blob.size <= TARGET_BYTES || (attempt === 5 && q === 0.4)) {
        const buf = new Uint8Array(await blob.arrayBuffer());
        let s = "";
        for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
        bmp.close();
        return { b64: btoa(s), bytes: buf.length };
      }
    }
    maxSide = Math.round(maxSide * 0.75);
  }
  throw new Error("could not compress photo");
}
