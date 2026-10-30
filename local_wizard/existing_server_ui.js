'use strict';
const host = document.getElementById('host'), ca = document.getElementById('ca'), jwt = document.getElementById('jwt');
const status = document.getElementById('status'), trust = document.getElementById('trust'), download = document.getElementById('download');
const caExpected = document.getElementById('ca-expected'), jwtExpected = document.getElementById('jwt-expected');
const normalized = value => value.replace(/[:\s]/g, '').toLowerCase();
function fingerprintsMatch() { return prepared && /^[a-f0-9]{64}$/.test(normalized(caExpected.value)) && normalized(caExpected.value) === normalized(prepared.caFingerprint) && /^[a-f0-9]{64}$/.test(normalized(jwtExpected.value)) && normalized(jwtExpected.value) === normalized(prepared.jwtFingerprint); }
function updateTrust() { trust.disabled = !fingerprintsMatch(); if (trust.disabled) trust.checked = false; download.disabled = trust.disabled || !trust.checked; }
for (const input of [caExpected, jwtExpected]) input.addEventListener('input', updateTrust);
let prepared = null, revision = 0;
function reset() { revision++; prepared = null; trust.checked = false; trust.disabled = true; download.disabled = true; caExpected.disabled = true; jwtExpected.disabled = true; caExpected.value = ''; jwtExpected.value = ''; status.textContent = ''; }
for (const input of [host, ca, jwt]) input.addEventListener('input', reset);
document.getElementById('check').addEventListener('click', async () => {
  reset();
  const current = revision;
  try {
    if (!ca.files[0] || !jwt.files[0] || ca.files[0].size > 8192 || jwt.files[0].size > 8192) throw new Error('공개 인증서와 공개키 파일을 선택하세요.');
    const result = await ExistingServer.build(host.value.trim(), await ca.files[0].text(), await jwt.files[0].text());
    if (current !== revision) return;
    prepared = result;
    status.textContent = 'CA 인증서 DER SHA-256:\n' + prepared.caFingerprint + '\nJWT 공개키 SHA-256 (DER):\n' + prepared.jwtFingerprint + '\n관리자에게서 별도로 받은 지문과 비교하세요. HAOS 설치 시 CA 유효성도 검사됩니다.';
    caExpected.disabled = false; jwtExpected.disabled = false; updateTrust();
  } catch (error) { if (current !== revision) return; status.textContent = '확인 실패: ' + error.message; }
});
trust.addEventListener('change', updateTrust);
download.addEventListener('click', () => {
  if (!fingerprintsMatch() || !trust.checked) return;
  const blob = new Blob([JSON.stringify(prepared.connection, null, 2) + '\n'], {type:'application/json'});
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = 'ha-connection.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
