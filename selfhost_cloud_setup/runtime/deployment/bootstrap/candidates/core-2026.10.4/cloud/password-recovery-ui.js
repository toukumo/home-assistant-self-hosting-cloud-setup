import {language, translate} from './cloud-i18n.js?v=3';
// The native HA route requests email only. New passwords are entered on the HTTPS link.
class SelfhostPasswordRecovery extends HTMLElement {
  constructor() { super();this._generation=0;this.busy=false; }
  set hass(value) {
    const previous=this._hass;
    this._hass=value;
    if(previous && previous.connection!==value?.connection) {
      this._generation++;this.busy=false;this.statusKey='';
      if(this.shadowRoot) {this.shadowRoot.querySelector('ha-button').disabled=false;this.shadowRoot.getElementById('email').disabled=false;}
    }
    this.localize();
  }
  get hass() { return this._hass; }
  localize() {
    const r=this.shadowRoot; if(!r) return;
    const t=key=>translate(this._hass,key);
    r.querySelector('ha-card').setAttribute('header',t('resetTitle'));
    r.querySelector('#help').textContent=t('resetRequestHelp');
    r.getElementById('email').setAttribute('label',t('email'));
    r.querySelector('ha-button').textContent=t('sendLink');
    r.querySelector('a').textContent=t('backLogin');
    r.querySelector('[role=status]').textContent=this.statusKey?t(this.statusKey):'';
  }
  connectedCallback() {
    this._generation++;this.busy=false;
    if (this.shadowRoot) {this.shadowRoot.querySelector('ha-button').disabled=false;this.shadowRoot.getElementById('email').disabled=false;this.localize();return;}
    const root=this.attachShadow({mode:'open'});
    root.innerHTML=`<style>:host{display:block}ha-card{max-width:480px;margin:24px auto;padding:24px}ha-input{display:block;margin:16px 0}p{line-height:1.5}a{color:var(--primary-color)}</style>
      <ha-card><p id="help"></p><ha-input id="email" type="email" autocomplete="username" required></ha-input>
      <p role="status" aria-live="polite"></p><ha-button></ha-button>
      <p><a href="/config/cloud/login"></a></p></ha-card>`;
    this.localize();
    root.querySelector('ha-button').addEventListener('click',()=>this.requestLink());
    root.getElementById('email').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing) this.requestLink();});
  }
  disconnectedCallback() { this._generation++;this.busy=false; }
  async requestLink() {
    if(this.busy||!this.isConnected) return;
    const generation=this._generation;
    const r=this.shadowRoot, email=(r.getElementById('email').value||'').trim();
    if([...email].length>320 || !/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(email)) {
      this.statusKey='invalidEmail'; this.localize(); return;
    }
    this.busy=true;const button=r.querySelector('ha-button'),input=r.getElementById('email');button.disabled=input.disabled=true;
    this.statusKey='';this.localize();
    const timeoutError=new Error('Recovery request deadline');let timer;
    try {
      const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>reject(timeoutError),30000);});
      await Promise.race([this.hass.callApi('POST','selfhost_cloud/password/request',{email,language:language(this.hass)}),deadline]);
      if(generation===this._generation&&this.isConnected)this.statusKey='requestSent';
    } catch(error) {
      if(generation===this._generation&&this.isConnected)this.statusKey=error===timeoutError?'mailDeliveryUnknown':error?.body?.error==='mail_not_configured'?'mailNotConfigured':'mailFailed';
    }
    finally {clearTimeout(timer);if(generation===this._generation&&this.isConnected){this.busy=false;button.disabled=false;input.disabled=false;this.localize();}}
  }
}
customElements.define('selfhost-password-recovery',SelfhostPasswordRecovery);
const recoveryInstalled = Symbol.for('selfhost-cloud.password-recovery-native-v1');
customElements.whenDefined('cloud-forgot-password').then(()=>{
  const component = customElements.get('cloud-forgot-password');
  if (!component || Object.hasOwn(component.prototype, recoveryInstalled)) return;
  const original=component.prototype.updated;
  component.prototype.updated=function(changed) {
    original?.call(this,changed);
    if(!this.shadowRoot) return;
    let shell=this.shadowRoot.querySelector('[data-selfhost-recovery]');
    if(!shell) {
      const style=document.createElement('style');style.textContent=':host > :not([data-selfhost-recovery]){display:none!important}';
      shell=document.createElement('hass-subpage');shell.dataset.selfhostRecovery='true';
      shell.setAttribute('back-path','/config/cloud/login');
      shell.append(document.createElement('selfhost-password-recovery'));this.shadowRoot.append(style,shell);
    }
    shell.setAttribute('header',translate(this.hass,'resetTitle'));
    shell.hass=this.hass;shell.narrow=this.narrow;shell.firstElementChild.hass=this.hass;
  };
  Object.defineProperty(component.prototype, recoveryInstalled, {value:true});
});
