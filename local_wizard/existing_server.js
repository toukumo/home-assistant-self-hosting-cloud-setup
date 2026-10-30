'use strict';

// This page uses only user-selected public material and never contacts the server.
(function (root) {
  const encoder = new TextEncoder();
  const b64 = bytes => {
    let binary = '';
    for (const value of bytes) binary += String.fromCharCode(value);
    return btoa(binary);
  };
  function hostname(value) {
    if (typeof value !== 'string' || value.length > 253 || value !== value.toLowerCase()) throw new Error('Invalid hostname');
    const labels = value.split('.');
    if (labels.length < 2 || labels.some(s => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(s)) ||
        ['test', 'local', 'localhost', 'invalid', 'example'].includes(labels[labels.length - 1]) || /^\d+$/.test(labels[labels.length - 1])) throw new Error('Invalid hostname');
    return value;
  }
  function pemBytes(text, label) {
    if (typeof text !== 'string' || text.length > 8192) throw new Error('Invalid public file');
    const match = text.trim().match(new RegExp('^-----BEGIN ' + label + '-----\\s+([A-Za-z0-9+/=\\r\\n]+)\\s+-----END ' + label + '-----$'));
    if (!match) throw new Error('Expected one ' + label + ' PEM file');
    try {
      const encoded = match[1].replace(/\s/g, '');
      if (encoded.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) throw new Error();
      const value = atob(encoded);
      if (!value.length || btoa(value) !== encoded) throw new Error();
      return Uint8Array.from(value, c => c.charCodeAt(0));
    } catch (_) { throw new Error('Invalid PEM encoding'); }
  }
  async function build(host, ca, jwt) {
    host = hostname(host);
    if (!globalThis.crypto || !globalThis.crypto.subtle) throw new Error('이 브라우저에서는 보안 암호화 기능을 사용할 수 없습니다. 최신 Chrome 또는 Edge를 사용하세요.');
    const caBytes = pemBytes(ca, 'CERTIFICATE');
    if (caBytes.length < 4 || caBytes[0] !== 0x30) throw new Error('Invalid certificate DER');
    let len = caBytes[1], start = 2;
    if (len & 0x80) {
      const count = len & 0x7f;
      if (count < 1 || count > 4 || caBytes.length < 2 + count || caBytes[2] === 0) throw new Error('Invalid certificate DER');
      len = 0;
      for (let i = 0; i < count; i++) len = len * 256 + caBytes[2 + i];
      start += count;
      if (len < 128) throw new Error('Invalid certificate DER');
    }
    if (len !== caBytes.length - start) throw new Error('Invalid certificate DER');
    const jwtBytes = pemBytes(jwt, 'PUBLIC KEY');
    const publicKey = await crypto.subtle.importKey('spki', jwtBytes, {name:'RSASSA-PKCS1-v1_5', hash:'SHA-256'}, true, ['verify']);
    if (publicKey.algorithm.modulusLength < 2048) throw new Error('RSA public key must be at least 2048 bits');
    const settings = {url:'https://' + host, ca:'/config/selfhost/ca.pem', jwt_public:'/config/selfhost/jwt-public.pem', snitun_websocket:'wss://' + host + '/snitun', certificate_issuer:true, migrate_existing_account:false};
    const fields = ['api_server','relayer_server','account_link_server','remotestate_server','acme_server'];
    const fragment = 'cloud:\n  mode: development\n' + fields.map(name => '  ' + name + ': ' + host + '\n').join('');
    const data = {'ca.pem':ca, 'jwt-public.pem':jwt, 'selfhost.json':JSON.stringify(settings, null, 2) + '\n', 'configuration-fragment.yaml':fragment};
    const files = Object.fromEntries(Object.entries(data).map(([name, value]) => [name,b64(encoder.encode(value))]));
    const fingerprint = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), x => x.toString(16).padStart(2, '0')).join(':');
    return {connection:{schema:1,files}, caFingerprint:await fingerprint(caBytes), jwtFingerprint:await fingerprint(jwtBytes)};
  }
  const api = {build, hostname, pemBytes};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ExistingServer = api;
})(globalThis);
