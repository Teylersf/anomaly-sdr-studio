/* Browser-local format inspection and key-based decryption. Keys never leave this page. */
(() => {
  'use strict';
  function fromHex(value) {
    const clean = String(value || '').replace(/\s+/g, '');
    if (!/^(?:[0-9a-f]{2})*$/i.test(clean)) throw new Error('Use complete hexadecimal byte pairs.');
    if (clean.length > 2097152) throw new Error('Input is limited to 1 MiB.');
    return Uint8Array.from(clean.match(/.{2}/g) || [], v => parseInt(v, 16));
  }
  const toHex = bytes => Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  async function decryptBytes({method, hex, keyHex, ivHex, aadHex = '', prefix = false, tagLength = 128, counterLength = 64}, subtle = globalThis.crypto?.subtle) {
    let bytes = fromHex(hex);
    const keyBytes = fromHex(keyHex);
    if (method === 'XOR') {
      if (keyBytes.length !== 1) throw new Error('The XOR test requires exactly one key byte.');
      return {bytes: bytes.map(b => b ^ keyBytes[0]), authenticated: false, method, validation: 'XOR output; no integrity or sender validation'};
    }
    if (!['AES-GCM', 'AES-CBC', 'AES-CTR'].includes(method)) throw new Error('Unsupported method.');
    if (!subtle) throw new Error('Web Crypto is unavailable; open the local app in a current browser.');
    if (![16, 24, 32].includes(keyBytes.length)) throw new Error('AES key must contain 16, 24 or 32 bytes.');
    const ivSize = method === 'AES-GCM' ? 12 : 16;
    const iv = prefix ? bytes.slice(0, ivSize) : fromHex(ivHex);
    if (prefix) bytes = bytes.slice(ivSize);
    if (!iv.length || (method !== 'AES-GCM' && iv.length !== 16)) throw new Error('CBC/CTR IV or counter must contain 16 bytes; GCM needs a nonce.');
    if (method === 'AES-GCM' && ![32, 64, 96, 104, 112, 120, 128].includes(Number(tagLength))) throw new Error('Unsupported GCM tag size.');
    if (method === 'AES-CTR' && (!Number.isInteger(Number(counterLength)) || counterLength < 1 || counterLength > 128)) throw new Error('CTR counter size must be 1–128 bits.');
    const params = method === 'AES-GCM' ? {name: method, iv, additionalData: fromHex(aadHex), tagLength: Number(tagLength)} :
      method === 'AES-CBC' ? {name: method, iv} : {name: method, counter: iv, length: Number(counterLength)};
    const key = await subtle.importKey('raw', keyBytes, {name: method}, false, ['decrypt']);
    try {
      const plain = new Uint8Array(await subtle.decrypt(params, key, bytes));
      return {bytes: plain, authenticated: method === 'AES-GCM', method,
        validation: method === 'AES-GCM' ? 'GCM authentication tag verified with the supplied key' : 'Decrypted bytes; this mode does not authenticate the contents'};
    } catch {
      throw new Error(method === 'AES-GCM' ? 'GCM authentication failed. Check key, nonce, tag, AAD and frame boundaries.' : 'Decryption failed. Check key, IV/counter, mode, padding and frame boundaries.');
    }
  }
  function inspectBytes(hex) {
    const bytes = fromHex(hex);
    let text = '';
    let utf8 = false;
    try { text = new TextDecoder('utf-8', {fatal: true}).decode(bytes); utf8 = true; } catch { text = Array.from(bytes, b => b >= 32 && b < 127 ? String.fromCharCode(b) : '·').join(''); }
    const findings = [];
    const printable = bytes.length ? Array.from(bytes).filter(b => (b >= 32 && b <= 126) || [9,10,13].includes(b)).length / bytes.length : 0;
    if (utf8) findings.push('Valid UTF-8 byte encoding');
    if (printable > .9 && bytes.length) findings.push('Mostly printable text; text alone does not validate a message');
    if (utf8) { try { JSON.parse(text); findings.push('JSON syntax parses'); } catch {} }
    if (bytes[0] === 0x1f && bytes[1] === 0x8b) findings.push('GZIP signature (compressed bytes)');
    if (bytes[0] === 0x50 && bytes[1] === 0x4b) findings.push('ZIP signature');
    if (bytes[0] === 0x89 && toHex(bytes.slice(1,4)) === '504e47') findings.push('PNG signature');
    if (!findings.length) findings.push('No supported format signature; encryption is not established');
    return {bytes, text: text.slice(0,4096), findings};
  }
  const api = {fromHex, toHex, decryptBytes, inspectBytes};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;
  window.AnomalySDRCrypto = api;
  function mount() {
    const root = document.getElementById('crypto-workbench');
    if (!root) return;
    root.innerHTML = `<style>.crypto-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.crypto-grid label{display:grid;gap:6px;font-size:11px;color:#94aeb8}.crypto-grid input,.crypto-grid textarea,.crypto-grid select{width:100%;background:#07161d;color:#d8edf1;border:1px solid #29434d;border-radius:7px;padding:9px;font-family:Consolas,monospace}.crypto-wide{grid-column:1/-1}.crypto-actions{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}.crypto-result{background:#07161d;border:1px solid #29434d;padding:14px;border-radius:8px;white-space:pre-wrap;word-break:break-all;font:11px/1.6 Consolas,monospace;max-height:240px;overflow:auto}.crypto-notice{font-size:11px;line-height:1.7;color:#97b1bc;margin:10px 0}.crypto-auto{display:flex;gap:8px;align-items:center;font-size:11px;color:#96bdc8}.crypto-auto input{width:auto}@media(max-width:650px){.crypto-grid{grid-template-columns:1fr}}</style>
      <p class="crypto-notice">Known methods with a supplied key. Candidate bytes can be wrong because demodulation, bit timing or framing is wrong. GCM can verify a tag; CBC, CTR and XOR output remain unauthenticated. Keys stay in this browser window and are not saved.</p>
      <div class="crypto-grid">
        <label>Method<select id="crypto-method"><option>AES-GCM</option><option>AES-CBC</option><option>AES-CTR</option><option>XOR</option></select></label>
        <label>Input frame layout<select id="crypto-layout"><option value="separate">Ciphertext; nonce supplied separately</option><option value="prefix">Nonce/counter prefix (GCM 12 bytes; CBC/CTR 16)</option></select></label>
        <label>Key in hex<input id="crypto-key" type="password" autocomplete="off" spellcheck="false" placeholder="Required: AES 16/24/32 bytes or XOR 1 byte"></label>
        <label>Nonce / IV / initial counter in hex<input id="crypto-iv" autocomplete="off" spellcheck="false" placeholder="GCM nonce; CBC/CTR 16 bytes"></label>
        <label>GCM tag length in bits<select id="crypto-tag"><option>128</option><option>96</option><option>64</option><option>32</option></select></label>
        <label>CTR counter bits<input id="crypto-counter" type="number" min="1" max="128" value="64"></label>
        <label class="crypto-wide">GCM additional authenticated data in hex (optional)<input id="crypto-aad" autocomplete="off" spellcheck="false" placeholder="Match the original packet's AAD"></label>
        <label class="crypto-wide">Packet / candidate bytes in hex (GCM includes trailing tag)<textarea id="crypto-input" rows="4" spellcheck="false" placeholder="Inspect a selected candidate or paste known ciphertext bytes"></textarea></label>
      </div>
      <div class="crypto-actions"><button type="button" id="crypto-inspect">Inspect byte formats</button><button type="button" id="crypto-decrypt">Try supplied-key decryption</button><button type="button" id="crypto-selftest">Run public GCM test vector</button><button type="button" id="crypto-clear">Clear keys and bytes</button></div>
      <label class="crypto-auto"><input id="crypto-auto" type="checkbox">Automatically try this explicit profile on new recognized packet bytes</label>
      <p class="crypto-notice" id="crypto-source">No received packet selected. Automatic attempts require a key and usable raw packet bytes. A profile does not discover encryption parameters.</p>
      <div class="crypto-result" id="crypto-result" aria-live="polite">Waiting for bytes. No plaintext inferred.</div>`;
    const $ = id => document.getElementById(id);
    let busy = false;
    const seen = new Set();
    const show = text => { $('crypto-result').textContent = text; };
    async function attempt(profile) {
      if (busy) return;
      busy = true;
      try {
        const result = await decryptBytes(profile || {method: $('crypto-method').value, hex: $('crypto-input').value,
          keyHex: $('crypto-key').value, ivHex: $('crypto-iv').value, aadHex: $('crypto-aad').value,
          prefix: $('crypto-layout').value === 'prefix', tagLength: Number($('crypto-tag').value), counterLength: Number($('crypto-counter').value)});
        const view = inspectBytes(toHex(result.bytes));
        show(`${result.validation}\n${result.bytes.length} bytes · ${result.method}\n\nUTF-8 / byte preview:\n${view.text}\n\nHex:\n${toHex(result.bytes).slice(0,8192)}\n\n${view.findings.join('\n')}`);
      } catch(error) { show('No validated plaintext: ' + error.message); }
      finally { busy = false; }
    }
    $('crypto-inspect').onclick = () => { try { const view=inspectBytes($('crypto-input').value); show(`${view.bytes.length} bytes\n${view.findings.join('\n')}\n\nByte preview:\n${view.text}`); } catch(error) { show(error.message); } };
    $('crypto-decrypt').onclick = () => attempt();
    $('crypto-selftest').onclick = async () => {
      $('crypto-source').textContent = 'Public AES-GCM test vector · synthetic cryptography verification · not received RF traffic';
      await attempt({method:'AES-GCM', hex:'0388dace60b6a392f328c2b971b2fe78ab6e47d42cec13bdf53a67b21257bddf',keyHex:'00000000000000000000000000000000',ivHex:'000000000000000000000000'});
    };
    $('crypto-clear').onclick = () => { for(const id of ['crypto-key','crypto-iv','crypto-aad','crypto-input']) $(id).value=''; $('crypto-auto').checked=false; seen.clear(); show('Keys and byte fields cleared.'); };
    window.addEventListener('anomaly-sdr-payload', event => {
      const detail = event.detail || {};
      $('crypto-input').value = String(detail.hex || '').slice(0,2097152);
      $('crypto-source').textContent = detail.label || 'Selected bytes; framing and origin not authenticated';
    });
    window.addEventListener('anomaly-sdr-status', event => {
      if (!$('crypto-auto').checked || !$('crypto-key').value || busy) return;
      const status = event.detail || {};
      const entries = [...(status.decoded || []), ...(status.recognition?.packets || [])];
      for (const entry of entries.slice(-10)) {
        const hex = entry.packet?.payload_hex || entry.raw_hex || entry.fields?.raw_msg;
        if (!hex || seen.has(hex)) continue;
        seen.add(hex); if(seen.size>100) seen.delete(seen.values().next().value);
        $('crypto-input').value=hex;
        $('crypto-source').textContent='Automatic attempt on packet bytes · '+(entry.protocol || entry.model || 'recognized record')+' · '+(status.source_label || status.mode);
        attempt(); break;
      }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
