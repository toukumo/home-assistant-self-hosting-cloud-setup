(function(){
"use strict";
const ca="-----BEGIN CERTIFICATE-----\nMIIDQjCCAiqgAwIBAgIUHV1krVSxC5P+2B/8GTunqZRk6DcwDQYJKoZIhvcNAQEL\nBQAwJzElMCMGA1UEAwwcU2VsZi1ob3N0ZWQgQ2xvdWQgcHJpdmF0ZSBDQTAeFw0y\nNjEwMzAwNjAzNTBaFw0zNjEwMjcwNjA4NTBaMCcxJTAjBgNVBAMMHFNlbGYtaG9z\ndGVkIENsb3VkIHByaXZhdGUgQ0EwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEK\nAoIBAQDo5AaZxBevkMOsBsoamKgqyhdeHv8jjegRtBg5nDljPqX7W1NLiXkPhDD9\nQkWQbYZekwdWhPCG0f8O4/TaH3a/Lcti8HZYFOlVUI4dHACkzff3oakDkP+2N6Gj\ndgj5FoxZCxfok0KsQx+1Tigy/ZQBGr01MWnz7s2m/E688rdHhHCwqUQcBo/PGRpP\nhNjBUaumYRASKvc2y+v0rt2z28+oIJepKO27VK50l1TxnwRH9zJ8trVt+TuE8T5q\nTV0F1bZLZ7lc6PFlPWZF51AYbN084p3bUzGrQeEfP0mwz4azrnvCttFb9iHxW8f+\nHbnwtLtb90q30RWBTZnX3wSk0u0jAgMBAAGjZjBkMBIGA1UdEwEB/wQIMAYBAf8C\nAQAwDgYDVR0PAQH/BAQDAgEGMB0GA1UdDgQWBBTH0QueC2RYAY2K2ym3NRh+oV80\nLDAfBgNVHSMEGDAWgBTH0QueC2RYAY2K2ym3NRh+oV80LDANBgkqhkiG9w0BAQsF\nAAOCAQEAJGjjMWlZTLFDI4g4Yga3m8gV7I4ObXG2ydLZrvw1L9/ShMa0RKMl9PwF\n+FbnHC5mFW4xvAbL33Ca16BQiYNJ479v6Mt2eyacU2SMtR31lfJyaFPHxwwagrfX\nXGwSZQ2E9yvklX1Mk1Z8j9p+RDAyZEjBDqHfA4XWLshdJA3cpGqjYHFLtxzZqACj\n7u8nRWQaI+KACSlAoRit3Afo5wJ/c+DYcEIXY6FlOnDCBN6XunLEFzu5/wnsNvR7\nIsqTQi8hm7cQXURJuKsu6iYKFlwIcGDDE/tXRmAJtAXcNhQTn/ArOR9ab+l0/TBn\nrg7yW0g31MAFToE1U6wM//vWZNgsow==\n-----END CERTIFICATE-----\n", jwt="-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtj+NaojQi5JFxgw5W02d\no74D5fcCD4ESk8oVHp2ni/v8PnCP0oTf5eHJfzjzgBKCGp5ZpiaOuaWzurkdi1iG\nRF9rEYBUIj5N5f5BhO/q9iocSSB/U4T0YZJCPsyj3/t8r1xHYuG14itnRQM/HuME\nPblNV7gepcVCgPYpdNN58HZH3MDPMIanWMRxQ1USUaZF6ZAIJp/WqF89+1MSzoDe\nokfci1iuKYYtIRXEUppnr66Uv1q6pCXCGBCv2Y9tWIz7+FhfbAzzaUvq+Nb1rSze\nshx7qPsxMG2pbteSNrUZF4vmGLqy7k58oa23jxM+5wdJDKQGRKgFsvHgPKx2DIan\n2wIDAQAB\n-----END PUBLIC KEY-----\n";
const result=document.getElementById('result');
const wait=async (f)=>{for(let i=0;i<150;i++){if(f())return;await new Promise(r=>setTimeout(r,100));}throw new Error('응답 시간 초과');};
document.getElementById('run').onclick=async()=>{
 result.textContent='검사 중';
 try {
 const w=window, d=document;
 const input=(id,val)=>{const e=d.getElementById(id);e.value=val;e.dispatchEvent(new w.Event('input',{bubbles:true}));};
 const file=(id,val)=>{const e=d.getElementById(id), t=new w.DataTransfer();t.items.add(new w.File([val],'fixture-public.pem',{type:'text/plain'}));e.files=t.files;e.dispatchEvent(new w.Event('input',{bubbles:true}));};
 input('host','api.example.com');file('ca',ca);file('jwt',jwt);d.getElementById('check').click();await wait(()=>!d.getElementById('ca-expected').disabled);
 const lines=d.getElementById('status').innerText.split('\n');input('ca-expected',lines[1]);input('jwt-expected','00'.repeat(32));if(!d.getElementById('trust').disabled)throw new Error('잘못된 지문 허용');input('jwt-expected',lines[3]);const trust=d.getElementById('trust');trust.click();if(d.getElementById('download').disabled)throw new Error('일치하는 지문 거부');
 let data=null;const originalURL=w.URL.createObjectURL;w.URL.createObjectURL=function(blob){blob.text().then(x=>data=JSON.parse(x));return originalURL.call(this,blob);};d.getElementById('download').click();await wait(()=>data!==null);if(data.schema!==1||Object.keys(data.files).sort().join(',')!=='ca.pem,configuration-fragment.yaml,jwt-public.pem,selfhost.json')throw new Error('다운로드 내용 오류');
 input('host','api2.example.com');if(!d.getElementById('download').disabled)throw new Error('입력 변경 후 승인 유지');result.textContent='PASS — 공개 인증서 지문 확인, 연결 파일 생성, 변경 후 재확인 모두 통과';
 } catch(e){result.textContent='FAIL — '+e.message;}
};

})();
