/**
 * Client-side proof-of-work solver.
 *
 * Finds a nonce N s.t. sha256(challenge + ":" + N) has at least
 * `difficulty` leading zero bits. Uses Web Crypto's subtle.digest;
 * works in any modern browser and is wholly synchronous from the
 * caller's view (returns a Promise).
 *
 * Yields cooperatively every CHUNK iterations so the UI thread can
 * paint a spinner during the search. Typical 18-bit difficulty
 * finishes in 30-300 ms on a 2024 laptop, low-single-digit seconds
 * on a 2018 phone.
 */
const CHUNK = 4096;

function leadingZeroBits(digest: Uint8Array): number {
  let n = 0;
  for (let i = 0; i < digest.length; i++) {
    const b = digest[i];
    if (b === 0) {
      n += 8;
      continue;
    }
    for (let shift = 7; shift >= 0; shift--) {
      if ((b >> shift) & 1) return n;
      n++;
    }
    return n;
  }
  return n;
}

export async function solvePow(
  challenge: string,
  difficulty: number,
  onProgress?: (tries: number) => void,
): Promise<string> {
  const enc = new TextEncoder();
  let n = 0;
  while (true) {
    for (let k = 0; k < CHUNK; k++) {
      const data = enc.encode(`${challenge}:${n}`);
      const hashBuf = await crypto.subtle.digest("SHA-256", data);
      const view = new Uint8Array(hashBuf);
      if (leadingZeroBits(view) >= difficulty) return String(n);
      n++;
    }
    onProgress?.(n);
    // Yield to the event loop so the spinner doesn't freeze.
    await new Promise((r) => setTimeout(r, 0));
  }
}
