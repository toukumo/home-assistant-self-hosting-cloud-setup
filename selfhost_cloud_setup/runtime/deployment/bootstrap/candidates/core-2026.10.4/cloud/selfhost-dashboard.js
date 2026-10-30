import {language, translate} from './cloud-i18n.js?v=1';
// Five features use HA components and WebSocket/HTTP APIs; routing is separate.
const escape = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const records=(value,field)=>Array.isArray(value)&&value.every(entry=>entry&&typeof entry==='object'&&!Array.isArray(entry)&&Object.hasOwn(entry,field)&&typeof entry[field]==='string'&&entry[field].length>0)?value:null;
const dateLocale=hass=>hass?.locale?.language||hass?.language||hass?.config?.language||'en';
const formatDate=(value,hass)=>{
  try {
    const preference=hass?.locale?.time_format;
    const hour12=preference==='12'?true:preference==='24'?false:preference==='system'
      ?new Intl.DateTimeFormat(undefined,{hour:'numeric'}).resolvedOptions().hour12:preference==='language'
      ?new Intl.DateTimeFormat(dateLocale(hass),{hour:'numeric'}).resolvedOptions().hour12:undefined;
    return new Intl.DateTimeFormat(dateLocale(hass),{
      dateStyle:'medium',timeStyle:'short',timeZone:hass?.locale?.time_zone==='local'?undefined:hass?.config?.time_zone||'UTC',
      ...(hour12===undefined?{}:{hourCycle:hour12?'h12':'h23'}),
    }).format(new Date(value));
  } catch { return translate(hass,'unknown'); }
};
class SelfhostDashboard extends HTMLElement {
  constructor() { super(); this.attachShadow({mode:'open'}); this.busy=false; this.data=null; this.message=''; this._generation=0; this._pendingFocus=null; this._reads=new WeakMap(); this._readTimers=new Map(); this._action=null; this._refreshing=null; this._refreshAgain=false; }
  set hass(v) {
    const previous=this._hass;
    const changed=language(previous)!==language(v)||dateLocale(previous)!==dateLocale(v)
      ||previous?.locale?.time_format!==v?.locale?.time_format
      ||previous?.locale?.time_zone!==v?.locale?.time_zone
      ||previous?.config?.time_zone!==v?.config?.time_zone;
    const connectionChanged=!!previous && previous.connection!==v?.connection;
    this._hass=v;
    if(connectionChanged) {
      this._generation++; this.busy=false; this.data=null; this.message=''; this.error=''; this._pendingFocus=null;
      this._discardReads(); this._discardAction();
    }
    if((changed||connectionChanged) && this.isConnected) this.render();
    if(this.isConnected && !this.data && !this.busy && !this.error) this.refresh();
  }
  connectedCallback() { this._generation++; this.busy=false; this.render(); if(this._hass) this.refresh(); this.timer=setInterval(()=>this.refresh(),30000); }
  disconnectedCallback() {
    this._generation++; this.busy=false; this._pendingFocus=null; clearInterval(this.timer);
    this._discardReads(); this._discardAction();
  }
  _discardReads() {
    this._refreshing=null; this._refreshAgain=false;
    for(const [timer,reject] of this._readTimers) { clearTimeout(timer); reject(new Error('Read abandoned')); }
    this._readTimers.clear(); this._reads=new WeakMap();
  }
  _read(hass,type) {
    const owner=hass.connection||hass;
    let reads=this._reads.get(owner);
    if(!reads) { reads=new Map(); this._reads.set(owner,reads); }
    if(reads.has(type)) return reads.get(type);
    let resolve,reject;
    const result=new Promise((yes,no)=>{resolve=yes; reject=no;});
    const timers=this._readTimers;
    const timer=setTimeout(()=>{
      if(timers.get(timer)!==reject) return;
      timers.delete(timer); reject(new Error('Read timed out'));
    },15000);
    timers.set(timer,reject); reads.set(type,result);
    const finish=(settle,value)=>{
      if(timers.get(timer)===reject) { clearTimeout(timer); timers.delete(timer); }
      if(reads.get(type)===result) reads.delete(type);
      settle(value);
    };
    // Keep the timed-out result until its wire request settles. Repeated refresh
    // must not create more unanswered requests; late results cannot update data.
    try { Promise.resolve(hass.callWS({type})).then(value=>finish(resolve,value),error=>finish(reject,error)); }
    catch(error) { finish(reject,error); }
    return result;
  }
  async refresh() {
    if(!this._hass || this.busy) return;
    const generation=this._generation,hass=this._hass,refresh={};
    this._refreshing=refresh; this.busy=true;
    try {
      const status=await this._read(hass,'cloud/status');
      if(generation!==this._generation) return;
      const results=await Promise.allSettled([
        this._read(hass,'backup/config/info'),
        this._read(hass,'config_entries/get'),
        this._read(hass,'webhook/list'),
        this._read(hass,'cloud/google_assistant/entities'),
        this._read(hass,'homeassistant/expose_entity/list'),
        hass.user?.is_admin===true?this._read(hass,'cloud/selfhost/maintenance/status'):Promise.resolve(null),
      ]);
      if(generation!==this._generation) return;
      const get=(i)=>results[i].status==='fulfilled'?results[i].value:null;
      this.data={status,backup:get(0)?.config,entries:get(1),hooks:get(2),entities:get(3),exposed:get(4)?.exposed_entities,maintenance:get(5)};
      this.error='';
    } catch { if(generation===this._generation) this.error='loadError'; }
    finally {
      if(generation===this._generation && this._refreshing===refresh) {
        this._refreshing=null; this.busy=false;
        if(this._refreshAgain) { this._refreshAgain=false; await this.refresh(); }
        else this.render();
      }
    }
  }
  _discardAction() {
    const action=this._action; this._action=null;
    if(action) action.abandon();
  }
  async action(work) {
    if(this.busy || this._action) return;
    const generation=this._generation,action={}; this._action=action;
    const deadline=new Promise(resolve=>{
      action.timer=setTimeout(()=>resolve({pending:true}),45000);
      action.abandon=()=>{clearTimeout(action.timer);resolve({abandoned:true});};
    });
    this.busy=true; this.message=''; this.render();
    let request;
    try { request=Promise.resolve(work()).then(()=>({ok:true}),()=>({ok:false})); }
    catch { request=Promise.resolve({ok:false}); }
    const finish=async result=>{
      if(this._action!==action || generation!==this._generation) return;
      this._action=null; this.message=result.ok?'saved':'requestError';
      if(this._refreshing) { this._refreshAgain=true; return; }
      this.busy=false; await this.refresh();
    };
    try {
      const result=await Promise.race([request,deadline]);
      if(this._action!==action || generation!==this._generation) return;
      if(result.pending) {
        // The wire mutation may still execute. Unlock reads, but keep mutations
        // disabled until its actual outcome or a connection/lifecycle change.
        this.busy=false; this.message='requestPending'; this.render();
        request.then(finish);
      } else { await finish(result); }
    } finally { clearTimeout(action.timer); }
  }
  async _restoreFocus(focus,generation) {
    const target=[...this.shadowRoot.querySelectorAll('ha-button')].find(button=>
      focus.id?button.id===focus.id:focus.entity?button.dataset.entity===focus.entity:button.getAttribute('href')===focus.href);
    if(!target) { if(this._pendingFocus===focus) this._pendingFocus=null; return; }
    try { await target.updateComplete; } catch { if(this._pendingFocus===focus) this._pendingFocus=null; return; }
    if(this._pendingFocus!==focus || generation!==this._generation || !this.isConnected) return;
    // A newer render owns a replacement target and will finish this request.
    if(!target.isConnected) return;
    const doc=this.ownerDocument;
    if(!this.shadowRoot.activeElement && (doc.activeElement===this || doc.activeElement===doc.body)
        && !target.hasAttribute('disabled')) target.focus({preventScroll:true});
    this._pendingFocus=null;
  }
  render() {
    const active=this.shadowRoot.activeElement;
    const focus=active?(active.id?{id:active.id}:active.dataset.entity?{entity:active.dataset.entity}:active.getAttribute('href')?{href:active.getAttribute('href')}:null):this._pendingFocus;
    this._pendingFocus=focus;const generation=this._generation;
    const t=(key,n)=>translate(this._hass,key,n);
    const d=this.data,s=d?.status,p=s?.prefs||{},disabled=(this.busy||this._action||this.error)?'disabled':'';
    const card=(id,title,state,body)=>`<ha-card outlined id="${id}"><div class="card-content"><div class="heading"><h2>${title}</h2><span class="state">${escape(state)}</span></div>${body}</div></ha-card>`;
    const backup=d?.backup; const agents=backup?.create_backup?.agent_ids||[];
    const backupKnown=!!backup && typeof backup==='object' && !Array.isArray(backup) && Array.isArray(agents) && agents.every(a=>typeof a==='string');
    const cloudBackup=backupKnown && agents.some(a=>a.startsWith('cloud.'));
    const entries=records(d?.entries,'domain')?.filter(e=>e.domain==='mobile_app');
    const hooks=p.cloudhooks||{};
    const googleEntities=records(d?.entities,'entity_id');
    const webhookEntries=records(d?.hooks,'webhook_id');
    const exposureKnown=!!d?.exposed && typeof d.exposed==='object' && !Array.isArray(d.exposed);
    const exposureText=entity=>!exposureKnown?t('failed'):d.exposed[entity]?.['cloud.google_assistant']?t('unexpose'):t('expose');
    const snapshot=d?.maintenance?.available===true?d.maintenance.snapshot:null;
    const age=snapshot?(Date.now()-Date.parse(snapshot.generated_at))/1000:NaN;
    const updater=snapshot?.schema===1&&snapshot.scope==='stored_updater_records_only'&&age>=0&&age<=900?snapshot:null;
    const updaterState=value=>{
      const names={checking:'updateChecking',qualified:'updateQualified',waiting:'updateWaiting',confirmed:'updateConfirmed',recovered:'updateRecovered',needs_diagnosis:'updateDiagnosis',unavailable:'updateUnavailable'};
      return typeof value==='string'&&Object.hasOwn(names,value)?names[value]:'updateUnavailable';
    };
    const updaterItem=item=>{
      const recorded=item?.record_updated_at;
      const stamp=typeof recorded==='string'&&recorded.length<=40&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(recorded)?Date.parse(recorded):NaN;
      const recordTime=Number.isFinite(stamp)&&stamp<=Date.parse(updater?.generated_at)?`<br><small>${t('updateRecordUpdated')}: <time data-maintenance-record datetime="${escape(recorded)}">${escape(formatDate(recorded,this._hass))}</time></small>`:'';
      return `${escape(t(updaterState(item?.state)))}${typeof item?.core_version==='string'&&/^20\d{2}\.\d{1,2}\.\d+$/.test(item.core_version)?` (Core ${escape(item.core_version)})`:''}${recordTime}`;
    };
    this.shadowRoot.innerHTML=`<style>
      :host{display:block;color:var(--primary-text-color);font:var(--ha-font-size-m,14px)/var(--ha-line-height-normal,1.5) var(--ha-font-family,sans-serif)}
      .content{padding:var(--ha-space-7,28px) var(--ha-space-5,20px);max-width:860px;margin:0 auto;display:flex;flex-direction:column;gap:var(--ha-space-6,24px)}ha-card{display:block}.card-content{padding:var(--ha-space-4,16px)}h2{font-size:var(--ha-font-size-xl,20px);font-weight:var(--ha-font-weight-normal,400);margin:0}.heading{display:flex;align-items:center;justify-content:space-between;gap:var(--ha-space-3,12px)}.state,p{color:var(--secondary-text-color)}.url{overflow-wrap:anywhere;color:var(--primary-text-color)}ha-button{margin-right:var(--ha-space-2,8px)}.rows{padding:0;list-style:none}.rows li>span{min-width:0;overflow-wrap:anywhere}.rows li{display:flex;justify-content:space-between;align-items:center;gap:var(--ha-space-3,12px);padding:var(--ha-space-2,8px) 0;border-bottom:1px solid var(--divider-color)}@media(max-width:550px){.heading,.rows li{flex-wrap:wrap}}
      </style><div class="content"><ha-alert alert-type="${this.error||this.message==='requestError'?'error':'info'}" role="status">${escape(t(this.error||this.message)||(s?`${t('server')}: ${s.cloud==='connected'?t('connected'):t('disconnected')}`:t('loading')))}</ha-alert>
      <ha-button appearance="filled" id="refresh" ${this.busy?'disabled':''}>${t('refresh')}</ha-button>
      ${this._hass?.user?.is_admin===true?`<section id="maintenance-status" aria-label="${escape(t('updateTitle'))}"><h2>${t('updateTitle')}</h2><p>${t('updateCandidate')}: ${updaterItem(updater?.candidate)}</p><p>${t('updateMaintenance')}: ${updaterItem(updater?.maintenance)}</p><p>${t('updateStoredHelp')}</p>${updater?`<p>${t('updateExported')}: ${escape(formatDate(updater.generated_at,this._hass))}</p>`:''}</section>`:''}
      ${card('remote',t('remote'),s?(s.remote_connected?t('connected'):t('disconnected')):t('unknown'),`<p>${t('remoteHelp')}</p>${s?.remote_domain?`<p class="url">https://${escape(s.remote_domain)}</p>`:''}<ha-button appearance="filled" id="remote-toggle" ${disabled||(!s?'disabled':'')}>${s?.remote_connected?t('disconnect'):t('connect')}</ha-button>`)}
      ${card('backup',t('backup'),backupKnown?(cloudBackup?t('backupSelected'):t('backupNotSelected')):t('failed'),`<p>${t('backupHelp')}</p>${backup?.last_completed_automatic_backup?`<p>${t('lastBackup')}: ${escape(formatDate(backup.last_completed_automatic_backup,this._hass))}</p>`:''}<ha-button appearance="plain" href="/config/backup">${t('manageBackup')}</ha-button>`)}
      ${card('companion',t('companion'),entries?t('apps',entries.length):t('failed'),`<p>${t('companionHelp')}</p><ha-button appearance="plain" href="/config/integrations/integration/mobile_app">${t('manageApps')}</ha-button>`)}
      ${card('google','Google Assistant',s?(p.google_enabled?(s.google_registered?t('connected'):t('linkNeeded')):t('off')):t('unknown'),`<p>${t('googleHelp')}</p><ha-button appearance="filled" id="google-toggle" ${disabled||(!s?'disabled':'')}>${p.google_enabled?t('disable'):t('enable')}</ha-button><ha-button appearance="filled" id="google-sync" ${disabled||(!p.google_enabled?'disabled':'')}>${t('sync')}</ha-button><ul class="rows">${(googleEntities||[]).map((e,i)=>`<li><span>${escape(e.entity_id)}</span><ha-button appearance="filled" data-expose="${i}" data-entity="${escape(e.entity_id)}" aria-label="${escape(exposureText(e.entity_id)+' '+e.entity_id)}" ${disabled||(!exposureKnown?'disabled':'')}>${exposureText(e.entity_id)}</ha-button></li>`).join('')}</ul>${d&&googleEntities===null?`<p>${t('entitiesFailed')}</p>`:''}${googleEntities?.length&&!exposureKnown?`<p>${t('loadError')}</p>`:''}`)}
      ${card('webhooks','Webhooks',s?t('hooks',Object.keys(hooks).length):t('unknown'),`<p>${t('hooksHelp')}</p><ul class="rows">${(webhookEntries||[]).map(h=>`<li><span>${escape((typeof h.name==='string'&&h.name)||(typeof h.domain==='string'&&h.domain)||'Webhook')}</span><span>${Object.hasOwn(hooks,h.webhook_id)&&hooks[h.webhook_id]?t('publicHook'):t('local')}</span></li>`).join('')}</ul>${d&&webhookEntries===null?`<p>${t('loadError')}</p>`:''}<ha-button appearance="plain" href="/config/cloud/webhooks">${t('manageHooks')}</ha-button>`)}
      </div>`;
    this.shadowRoot.querySelector('#refresh').onclick=()=>this.refresh();
    this.shadowRoot.querySelector('#remote-toggle').onclick=()=>this.action(()=>this._hass.callWS({type:s?.remote_connected?'cloud/remote/disconnect':'cloud/remote/connect'}));
    this.shadowRoot.querySelector('#google-toggle').onclick=()=>this.action(()=>this._hass.callWS({type:'cloud/update_prefs',google_enabled:!p.google_enabled}));
    this.shadowRoot.querySelector('#google-sync').onclick=()=>this.action(async()=>{await this._hass.callApi('POST','cloud/google_actions/sync');});
    for(const button of this.shadowRoot.querySelectorAll('[data-expose]')) button.onclick=()=>{
      if(!exposureKnown) return;
      const entity=googleEntities[Number(button.dataset.expose)].entity_id;
      return this.action(()=>this._hass.callWS({type:'homeassistant/expose_entity',assistants:['cloud.google_assistant'],entity_ids:[entity],should_expose:!d.exposed?.[entity]?.['cloud.google_assistant']}));
    };
    if(focus) void this._restoreFocus(focus,generation);
  }
}
customElements.define('selfhost-cloud-dashboard',SelfhostDashboard);
