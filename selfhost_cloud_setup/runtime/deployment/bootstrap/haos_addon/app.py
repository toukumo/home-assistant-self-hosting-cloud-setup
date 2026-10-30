#!/usr/bin/env python3
"""Admin-only HAOS ingress for a fresh Cloud install and guarded recovery."""
import hmac
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from connection_file import unpack
from install_ha import install, configuration, cloud_state_exists
from recover_ha import recover
from stage_ha import read_file, stage, parse_object
from wizard import KNOWN
from voice_probe import probe

CONFIG=Path('/homeassistant')
CANDIDATE=Path(__file__).resolve().parents[1]/'candidates/core-2026.10.4/cloud'
MAX_BODY=65536
PAGE='''<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Cloud setup</title>
<style>body{font:16px system-ui;max-width:40rem;margin:2rem auto;padding:1rem}button,input{margin:.7rem 0;padding:.7rem}pre{white-space:pre-wrap}</style>
<h1>Self-hosted Cloud</h1><p id="intro"></p><input id="file" type="file" accept=".json,application/json"><br><button id="install"></button> <button id="recover"></button><pre id="status" role="status"></pre><section><h2 id="voice-title"></h2><p id="voice-note"></p><label>STT <input id="stt-uri" placeholder="tcp://192.168.1.10:10300"></label> <button id="stt-check"></button><p id="stt-status" role="status"></p><label>TTS <input id="tts-uri" placeholder="tcp://192.168.1.10:10200"></label> <button id="tts-check"></button><p id="tts-status" role="status"></p><label>TTS (JA) <input id="tts_ja-uri" placeholder="tcp://192.168.1.10:10201"></label> <button id="tts_ja-check"></button><p id="tts_ja-status" role="status"></p></section>
<script>const token=TOKEN;const ko=navigator.language.startsWith('ko'),ja=navigator.language.startsWith('ja');const text=ko?['공개 연결 파일 ha-connection.json을 선택하세요. 설치 중 Home Assistant가 잠시 재시작됩니다.','설치 및 연결 준비','설치 전 설정으로 복구','작업 중… 완료 후 이 화면을 다시 열어 상태를 확인하세요.']:ja?['公開接続ファイル ha-connection.json を選択してください。作業中 Home Assistant が再起動します。','インストール','インストール前の設定に復元','処理中… 完了後にこの画面を開き直して確認してください。']:['Select the public ha-connection.json file. Home Assistant restarts during setup.','Install integration','Recover original configuration','Working… Reopen this page after completion to check status.'];document.querySelector('#intro').textContent=text[0];document.querySelector('#install').textContent=text[1];document.querySelector('#recover').textContent=text[2];const status=document.querySelector('#status');const messages={ready:ko?'연결 파일을 선택해 설치를 시작하세요.':ja?'接続ファイルを選択して開始してください。':'Select a connection file to begin.',installed_login_next:ko?'설치가 완료됐습니다. 설정 → Home Assistant Cloud에서 새 Cloud 계정으로 로그인하세요.':ja?'インストールが完了しました。設定 → Home Assistant Cloud で新しい Cloud アカウントにログインしてください。':'Installed. Open Settings → Home Assistant Cloud and sign in with your new Cloud account.',original_restored:ko?'설치 전 설정으로 복구했습니다. 설치 자료와 원본은 보존했습니다.':ja?'元の設定に復元しました。インストール資料と元のファイルは保持されています。':'Original configuration restored. Installation files and originals were retained.',installation_failed_original_preserved:ko?'설치에 실패해 원래 설정으로 되돌렸습니다. 복구 자료를 보존하세요.':ja?'インストールに失敗したため元に戻しました。復旧資料を保持してください。':'Installation failed and the original configuration was retained. Keep the recovery files.'};const failures=new Set(['stop_failed_core_state_unknown','start_failed_core_state_unknown','configuration_check_failed_core_stopped','recovery_required_core_stopped','recovery_refused_core_stopped']);const failureText=ko?'작업이 중단됐습니다. 설정 → 시스템에서 Home Assistant 상태를 확인하세요. 설정 검사가 실패했다면 설치 전 설정으로 복구할 수 있습니다. 기존 연결과 복구 파일을 보존하세요.':ja?'処理を中断しました。設定 → システムで Home Assistant の状態を確認してください。構成チェックに失敗した場合は元の設定に復元できます。既存の接続と復旧ファイルを保持してください。':'Operation stopped. Check Home Assistant status in Settings → System. If configuration validation failed, recover the original configuration. Preserve existing connections and recovery files.';const busyStates=new Set(['stopping_core','stopping_core_for_recovery','checking_configuration','starting_core']);function describe(r){const state=r.state||r.status?.state;if(failures.has(state))return failureText;if(r.error)return ko?'작업을 진행하지 못했습니다. 기존 Cloud 연결이나 변경된 파일이 있다면 보존하고 복구 기록을 확인하세요.':ja?'処理を中断しました。既存の接続や変更ファイルを保持し、復旧記録を確認してください。':'Operation stopped. Preserve existing Cloud state and changed files and inspect the recovery record.';return messages[state]||(busyStates.has(state)?text[3]:failureText);}async function action(path,body){status.textContent=text[3];for(const b of document.querySelectorAll('button'))b.disabled=true;try{const r=await fetch('./'+path,{method:'POST',headers:{'Content-Type':'application/json','X-Setup-Token':token},body:JSON.stringify(body)});status.textContent=describe(await r.json());}catch(e){status.textContent=text[3];}finally{for(const b of document.querySelectorAll('button'))b.disabled=false;}}document.querySelector('#install').onclick=async()=>{const f=document.querySelector('#file').files[0];if(f)try{await action('install',JSON.parse(await f.text()));}catch(e){status.textContent='Invalid connection file';}};document.querySelector('#recover').onclick=()=>action('recover',{});fetch('./status').then(r=>r.json()).then(r=>status.textContent=describe(r));const labels=ko?['음성 서비스 점검','이 설치 앱에서 접근할 수 있는 사설 IP를 입력하세요. 이 점검은 음성 서비스를 선택하거나 연결하지 않습니다.','연결 점검','응답 확인됨 (음성 처리 미검증)','연결되지 않음. 장비 상태를 확인하고 다시 점검하세요.','이 유형의 음성 서비스가 아닙니다.','사용 가능한 음성 모델이 없습니다.','잘못된 응답 또는 주소입니다.']:ja?['音声サービスの確認','このセットアップアプリから接続できるプライベート IP を入力してください。この確認はサービスの選択・接続を行いません。','接続を確認','応答あり（音声処理は未検証）','接続できません。機器を確認して再試行してください。','サービスの種類が違います。','利用可能な音声モデルがありません。','応答またはアドレスが無効です。']:['Voice service check','Enter a private IP reachable from this setup app. This check does not select or connect a voice service.','Check connection','Responded (audio processing not tested)','Unavailable. Check the device and try again.','Wrong service type.','No installed voice model.','Invalid response or address.'];document.querySelector('#voice-title').textContent=labels[0];document.querySelector('#voice-note').textContent=labels[1];document.querySelector('#file').addEventListener('change',async e=>{const f=e.target.files[0];if(!f||f.size>65536)return;try{const bundle=JSON.parse(await f.text());const conf=JSON.parse(atob(bundle.files['selfhost.json']));for(const kind of ['stt','tts','tts_ja'])if(typeof conf.local_voice?.[kind]==='string')document.querySelector('#'+kind+'-uri').value=conf.local_voice[kind];}catch(_){}});for(const kind of ['stt','tts','tts_ja']){const button=document.querySelector('#'+kind+'-check'),out=document.querySelector('#'+kind+'-status');button.textContent=labels[2];button.onclick=async()=>{button.disabled=true;try{const r=await fetch('./voice/probe',{method:'POST',headers:{'Content-Type':'application/json','X-Setup-Token':token},body:JSON.stringify({kind:kind==='tts_ja'?'tts':kind,endpoint:document.querySelector('#'+kind+'-uri').value})});const d=await r.json();out.textContent=({reachable:labels[3],unreachable:labels[4],wrong_service:labels[5],not_installed:labels[6],invalid_response:labels[7]})[d.state]||labels[7];}catch(_){out.textContent=labels[4];}finally{button.disabled=false;}};}</script></html>'''


class Supervisor:
    def call(self, path, method='GET'):
        token=os.environ.get('SUPERVISOR_TOKEN')
        if not token:raise RuntimeError('Supervisor authentication unavailable')
        request=urllib.request.Request('http://supervisor'+path,headers={'Authorization':'Bearer '+token},method=method)
        try:
            with urllib.request.urlopen(request,timeout=120) as response:
                data=parse_object(response.read(65537))
            if data.get('result')!='ok':raise ValueError('Supervisor rejected request')
            return data.get('data',{})
        except Exception:raise RuntimeError('Supervisor operation failed; inspect saved status before retrying') from None



class Controller:
    def __init__(self,config=CONFIG,candidate=CANDIDATE,supervisor=None,state_dir=Path('/data')):
        self.config,self.candidate,self.supervisor,self.state_dir=Path(config),Path(candidate),supervisor or Supervisor(),Path(state_dir)
        self.lock=threading.Lock()
    def status(self):
        try:return parse_object(read_file(self.state_dir/'setup-status.json',8192))
        except FileNotFoundError:return {'state':'ready','supported_core':'2026.10.4'}
    def save(self,state):
        path=self.state_dir/'setup-status.json'
        fd,name=tempfile.mkstemp(dir=self.state_dir,prefix='.setup-status-')
        with os.fdopen(fd,'w') as stream:json.dump({'state':state},stream);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    def core_operation(self, operation):
        failures={'stop':'stop_failed_core_state_unknown',
                  'check':'configuration_check_failed_core_stopped',
                  'start':'start_failed_core_state_unknown'}
        try:return self.supervisor.call('/core/'+operation,'POST')
        except Exception:
            self.save(failures[operation])
            raise
    def run(self,action,data):
        if not self.lock.acquire(blocking=False):raise ValueError('Another operation is running')
        try:
            info=self.supervisor.call('/core/info')
            if info.get('version')!='2026.10.4':raise ValueError('This installer requires Core 2026.10.4')
            if action=='install':
                if (self.config/'.selfhost-fresh-install-lock').exists() and not (self.config/'.selfhost-fresh-install-lock/RECOVERED.txt').exists():raise ValueError('Installation record exists; inspect status or recover')
                if cloud_state_exists(self.config):raise ValueError('Cloud account state exists; preserve it for reviewed recovery')
                configuration(read_file(self.config/'configuration.yaml'))
                with tempfile.TemporaryDirectory(dir=self.state_dir,prefix='.connection-') as tmp:
                    package=unpack(json.dumps(data).encode(),Path(tmp)/'package')
                    # Validate connection and candidate before stopping HA.
                    stage(Path(tmp)/'checked',self.candidate,KNOWN['2026.10.4'],package,'2026.10.4')
                    self.save('stopping_core')
                    self.core_operation('stop')
                    try:install(self.config,package,self.candidate,'2026.10.4')
                    except Exception:
                        try:
                            journal=self.config/'.selfhost-fresh-install-lock'
                            if (journal/'installation.json').exists() and not (journal/'RECOVERED.txt').exists():recover(self.config)
                            elif (self.config/'custom_components/cloud').exists() or (self.config/'selfhost').exists():raise ValueError('Existing data requires review')
                        except Exception:self.save('recovery_required_core_stopped')
                        else:
                            try:self.core_operation('start')
                            except Exception:pass  # The saved state records uncertain Core status.
                            else:self.save('installation_failed_original_preserved')
                        raise RuntimeError('Installation failed; inspect the saved recovery status') from None
            elif action=='recover':
                if data!={}:raise ValueError('Recovery takes no parameters')
                if cloud_state_exists(self.config):raise ValueError('Cloud account state exists; preserve it for reviewed recovery')
                record=self.config/'.selfhost-fresh-install-lock/installation.json'
                if not record.exists():raise ValueError('No recoverable installation record')
                self.save('stopping_core_for_recovery')
                self.core_operation('stop')
                try:recover(self.config)
                except Exception:
                    self.save('recovery_refused_core_stopped')
                    raise RuntimeError('Recovery refused; preserve files and review changes before starting Core') from None
            else:raise ValueError('Invalid action')
            self.save('checking_configuration')
            self.core_operation('check')
            self.save('starting_core')
            self.core_operation('start')
            self.save('installed_login_next' if action=='install' else 'original_restored')
            return self.status()
        finally:self.lock.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def allowed(self):return self.client_address[0]=='172.30.32.2'
    def reply(self,status,data,html=False):
        body=data.encode() if html else json.dumps(data).encode()
        self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'");self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if not self.allowed():self.reply(403,{'error':'Ingress required'});return
        if self.path=='/':self.reply(200,PAGE.replace('TOKEN',json.dumps(self.server.token)),True)
        elif self.path=='/status':self.reply(200,self.server.controller.status())
        else:self.reply(404,{'error':'Not found'})
    def do_POST(self):
        if (not self.allowed() or self.path not in ('/install','/recover','/voice/probe')
                or self.headers.get_all('X-Setup-Token',[]) != [self.server.token]):
            self.reply(403,{'error':'Ingress session required'});return
        try:
            if (len(self.headers.get_all('Content-Length',[]))!=1 or self.headers.get('Transfer-Encoding')
                    or self.headers.get_content_type()!='application/json'):raise ValueError('Invalid request')
            n=int(self.headers['Content-Length'])
            if not 0<n<=MAX_BODY:raise ValueError('Connection file too large')
            self.connection.settimeout(15);data=parse_object(self.rfile.read(n))
            if self.path == '/voice/probe':
                if set(data)!={'endpoint','kind'}:raise ValueError('Invalid voice probe')
                if not self.server.probe_slots.acquire(blocking=False):
                    self.reply(429,{'error':'Too many checks'});return
                try: result=asyncio.run(probe(data['endpoint'],data['kind']))
                finally:self.server.probe_slots.release()
            else:result=self.server.controller.run(self.path[1:],data)
        except (ValueError,RuntimeError,OSError):
            self.reply(400,{'error':'Operation stopped; check status and preserve recovery files','status':self.server.controller.status()});return
        self.reply(200,result)


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def handle_error(self,*args):pass

if __name__=='__main__':
    server=Server(('0.0.0.0',8099),Handler);server.token=secrets.token_urlsafe(32);server.controller=Controller();server.probe_slots=threading.BoundedSemaphore(2);server.serve_forever()
