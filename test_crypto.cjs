const assert = require('node:assert/strict');
const {webcrypto} = require('node:crypto');
const api = require('./crypto-workbench.js');
(async () => {
  const vector = {method:'AES-GCM',hex:'0388dace60b6a392f328c2b971b2fe78ab6e47d42cec13bdf53a67b21257bddf',keyHex:'00000000000000000000000000000000',ivHex:'000000000000000000000000'};
  const valid = await api.decryptBytes(vector,webcrypto.subtle);
  assert.equal(api.toHex(valid.bytes),'00000000000000000000000000000000');
  assert.equal(valid.authenticated,true);
  await assert.rejects(api.decryptBytes({...vector,keyHex:'10000000000000000000000000000000'},webcrypto.subtle),/authentication failed/);
  await assert.rejects(api.decryptBytes({...vector,hex:vector.hex.slice(0,-2)+'00'},webcrypto.subtle),/authentication failed/);
  await assert.rejects(api.decryptBytes({...vector,keyHex:'00'},webcrypto.subtle),/16, 24 or 32/);
  assert.throws(()=>api.fromHex('abc'),/pairs/);
  const plaintext = new TextEncoder().encode('Anomaly SDR Studio offline cipher round-trip');
  for(const method of ['AES-CBC','AES-CTR']) {
    const keyBytes = new Uint8Array(16).fill(7);
    const iv = new Uint8Array(16).fill(3);
    const key = await webcrypto.subtle.importKey('raw',keyBytes,method,false,['encrypt']);
    const params=method==='AES-CBC'?{name:method,iv}:{name:method,counter:iv,length:64};
    const ciphertext=new Uint8Array(await webcrypto.subtle.encrypt(params,key,plaintext));
    const result=await api.decryptBytes({method,hex:api.toHex(ciphertext),keyHex:api.toHex(keyBytes),ivHex:api.toHex(iv)},webcrypto.subtle);
    assert.deepEqual(result.bytes,plaintext);
    assert.equal(result.authenticated,false);
  }
  const xor=await api.decryptBytes({method:'XOR',hex:'202322',keyHex:'41'});
  assert.equal(api.toHex(xor.bytes),'616263');
  assert.equal(xor.authenticated,false);
  const text=api.inspectBytes('7b226f6b223a747275657d');
  assert(text.findings.includes('JSON syntax parses'));
  assert.equal(api.inspectBytes('ff00').findings.some(v=>v.includes('UTF-8')),false);
  console.log('Crypto checks passed: public GCM vector, bad key/tag, CBC/CTR round trips, XOR, bounded byte formats.');
})().catch(error=>{console.error(error);process.exitCode=1});
