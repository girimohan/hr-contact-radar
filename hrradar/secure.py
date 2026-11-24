"""Encryption for running the radar on GitHub: the published page and the saved state are unreadable without
the passphrase (GitHub secret RADAR_PASSPHRASE).

Format: b"HRR1" + salt(16) + iv(12) + AES-256-GCM(gzip(data)), key = PBKDF2-HMAC-SHA256(passphrase, salt).
The page decrypts the same format in the browser with WebCrypto, so contacts never sit on GitHub in clear text.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import os
import tarfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"HRR1"
ITERATIONS = 600_000
MIN_PASSPHRASE = 12

# Files a run reads back the next day. Contacts live in the private ones; the data/ ones hold no people.
STATE_FILES = ["private/nav_store.json", "private/site_cache.json", "data/seen.json", "data/domains.json",
               "data/mx.json", "data/nav_cursor.json"]


class BadPassphrase(Exception):
    pass


def passphrase_from_env(name: str = "RADAR_PASSPHRASE") -> str:
    p = os.environ.get(name, "")
    if len(p) < MIN_PASSPHRASE:
        raise SystemExit(f"{name} is missing or shorter than {MIN_PASSPHRASE} characters. "
                         f"Set it as a repository secret: gh secret set {name}")
    return p


def _key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, ITERATIONS, dklen=32)


def encrypt(data: bytes, passphrase: str) -> bytes:
    salt, iv = os.urandom(16), os.urandom(12)
    return MAGIC + salt + iv + AESGCM(_key(passphrase, salt)).encrypt(iv, gzip.compress(data, 9), MAGIC)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    if blob[:4] != MAGIC:
        raise ValueError("not an hr-contact-radar encrypted file")
    salt, iv, ct = blob[4:20], blob[20:32], blob[32:]
    try:
        return gzip.decompress(AESGCM(_key(passphrase, salt)).decrypt(iv, ct, MAGIC))
    except Exception as e:   # InvalidTag
        raise BadPassphrase("wrong passphrase, or the file was changed") from e


def seal_page(html: str, passphrase: str, title: str = "HR contact radar") -> str:
    """A small page that asks for the passphrase and then shows the dashboard."""
    payload = base64.b64encode(encrypt(html.encode("utf-8"), passphrase)).decode("ascii")
    return (LOCK_PAGE.replace("__TITLE__", title).replace("__ITER__", str(ITERATIONS))
            .replace("__PAYLOAD__", payload))


def pack_state(root: Path, passphrase: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel in STATE_FILES:
            p = root / rel
            if p.is_file():
                tar.add(p, arcname=rel)
    return encrypt(buf.getvalue(), passphrase)


def unpack_state(blob: bytes, root: Path, passphrase: str) -> list[str]:
    allowed = set(STATE_FILES)
    out = []
    with tarfile.open(fileobj=io.BytesIO(decrypt(blob, passphrase)), mode="r") as tar:
        for m in tar.getmembers():
            if m.name not in allowed or not m.isfile():   # only our own files, never paths elsewhere
                continue
            dest = root / m.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(tar.extractfile(m).read())
            out.append(m.name)
    return out


LOCK_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>__TITLE__</title>
<style>
 :root{color-scheme:light dark;--bg:#f6f7f9;--fg:#1d2330;--mut:#667085;--card:#fff;--line:#d9dde5;--acc:#2f5bd3}
 @media (prefers-color-scheme:dark){:root{--bg:#14171d;--fg:#e7eaf0;--mut:#9aa3b2;--card:#1d222b;--line:#323947;--acc:#7c9cff}}
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);
      font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
 form{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:28px;width:min(360px,88vw)}
 h1{font-size:18px;margin:0 0 4px} p{margin:0 0 18px;color:var(--mut);font-size:13px}
 input[type=password]{box-sizing:border-box;width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;
      background:transparent;color:inherit;font:inherit}
 label{display:flex;gap:8px;align-items:center;margin:12px 0 16px;font-size:13px;color:var(--mut)}
 button{width:100%;padding:10px;border:0;border-radius:8px;background:var(--acc);color:#fff;font:inherit;cursor:pointer}
 button:disabled{opacity:.6;cursor:wait} #err{color:#d24;font-size:13px;min-height:1.5em;margin:10px 0 0}
</style></head><body>
<form id="f" autocomplete="on">
 <h1>__TITLE__</h1>
 <p>The leads and contacts on this page are encrypted. Enter the passphrase to open them.</p>
 <input type="text" name="username" value="hr-contact-radar" autocomplete="username" hidden>
 <input id="pw" type="password" autocomplete="current-password" placeholder="Passphrase" autofocus required>
 <label><input id="rem" type="checkbox"> Remember on this device</label>
 <button id="go">Open</button>
 <div id="err" role="alert"></div>
</form>
<script>
const B64 = "__PAYLOAD__", ITER = __ITER__, KEY = "hr-contact-radar:pass";
const bytes = Uint8Array.from(atob(B64), c => c.charCodeAt(0));
const enc = new TextEncoder();
async function open_(pass) {
  const magic = bytes.slice(0, 4), salt = bytes.slice(4, 20), iv = bytes.slice(20, 32), ct = bytes.slice(32);
  const base = await crypto.subtle.importKey("raw", enc.encode(pass), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey({name: "PBKDF2", salt, iterations: ITER, hash: "SHA-256"},
                                            base, {name: "AES-GCM", length: 256}, false, ["decrypt"]);
  const gz = await crypto.subtle.decrypt({name: "AES-GCM", iv, additionalData: magic}, key, ct);
  const html = await new Response(new Blob([gz]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
  document.open(); document.write(html); document.close();
}
const f = document.getElementById("f"), pw = document.getElementById("pw"), go = document.getElementById("go"),
      err = document.getElementById("err"), rem = document.getElementById("rem");
f.addEventListener("submit", async e => {
  e.preventDefault(); go.disabled = true; err.textContent = "";
  try {
    if (rem.checked) localStorage.setItem(KEY, pw.value); else localStorage.removeItem(KEY);
    await open_(pw.value);
  } catch (x) { err.textContent = "That passphrase doesn't open this page."; go.disabled = false;
                localStorage.removeItem(KEY); }
});
const saved = localStorage.getItem(KEY);
if (saved) { rem.checked = true; pw.value = saved; open_(saved).catch(() => { localStorage.removeItem(KEY); pw.value = ""; }); }
</script></body></html>
"""
