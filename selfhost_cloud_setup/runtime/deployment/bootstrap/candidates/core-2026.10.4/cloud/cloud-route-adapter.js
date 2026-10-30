// Preserve the native Cloud UI. Only retired-provider/payment UI is adapted.
// Selectors are bound to the pinned native Cloud account/assistants components;
// no native template, route, state, event handler or stylesheet is replaced.
const installed = Symbol.for('selfhost-cloud.native-ui-minimal-v1');
const googleSettings = '/config/voice-assistants/assistants?historyBack=1';
const styleMarker = 'data-selfhost-native-ui';

function style(root, text) {
  if (root.querySelector(`style[${styleMarker}]`)) return;
  const element = document.createElement('style');
  element.setAttribute(styleMarker, '');
  element.textContent = text;
  root.append(element);
}

function text(span, value) {
  if (!span || typeof value !== 'string' || !value) return;
  // Lit owns the surrounding comment anchors. Editing the single bound text
  // node keeps those anchors alive for later language/status updates.
  const nodes = [...span.childNodes].filter(node => node.nodeType === 3);
  if (nodes.length === 1 && nodes[0].data !== value) nodes[0].data = value;
}

function overview(host) {
  const root = host.shadowRoot;
  if (!root) return;
  providerCopy(host);
  if (host.parentNode) style(host.parentNode, 'cloud-account-onboarding { display:none!important; }');
  style(root, `
    /* This card is the official paid-service funding/renewal summary only. */
    .summary-card,
    ha-button[href="https://account.nabucasa.com"],
    ha-button[href^="https://account.nabucasa.com/"],
    [data-selfhost-unavailable="subscription"] { display:none!important; }
  `);
  const localize = host.hass?.localize?.bind(host.hass);
  const subscription = localize?.('ui.panel.config.cloud.account.subscription');
  const header = root.querySelector('.account-header');
  const account = header?.closest('ha-card');
  if (subscription && account) {
    for (const row of account.querySelectorAll('ha-list-item-base')) {
      // Keep the native email reveal, account header and sign-out action.
      if (!row.querySelector('.email-line') &&
          row.querySelector('[slot="headline"]')?.textContent === subscription) {
        row.setAttribute('data-selfhost-unavailable', 'subscription');
      }
    }
  }
  const row = root.querySelector(`ha-list-item-button[href="${googleSettings}"]`);
  if (!row) return;
  // Native HA combines Alexa and Google in one row. Keep its original icon,
  // navigation and layout; change only the two bound labels to Google status.
  text(row.querySelector('[slot="headline"]'), 'Google Assistant');
  const labels = {
    en: ['Google Assistant not connected', 'Google Assistant connected'],
    ko: ['Google Assistant 연결 안 됨', 'Google Assistant 연결됨'],
    ja: ['Google Assistant 未接続', 'Google Assistant 接続済み'],
  };
  const language = (host.hass?.locale?.language || host.hass?.language || 'en').split('-')[0];
  text(row.querySelector('[slot="supporting-text"]'),
    (labels[language] || labels.en)[host.cloudStatus?.google_registered ? 1 : 0]);
}

function assistants(host) {
  if (!host.shadowRoot) return;
  // The sibling cloud-google-pref and native Assist/device UI stay untouched.
  style(host.shadowRoot, 'cloud-alexa-pref { display:none!important; }');
}

function existing(name, update) {
  // A lazy native element can finish its first update before whenDefined's
  // callback runs. Adapt already-rendered instances once, including HA's nested
  // shadow roots. This never replays native updated(), polls, or observes DOM.
  const roots = [document], seen = new Set();
  let remaining = 20000;
  while (roots.length && remaining > 0) {
    const root = roots.pop();
    if (seen.has(root) || typeof root.querySelectorAll !== 'function') continue;
    seen.add(root);
    for (const element of root.querySelectorAll('*')) {
      if (--remaining < 0) return;
      if (element.localName === name) update(element);
      if (element.shadowRoot) roots.push(element.shadowRoot);
    }
  }
}

function adapt(name, update) {
  // Register callbacks without delaying module load for lazy native routes.
  customElements.whenDefined(name).then(() => {
    // HA's registration/decorator path may resolve whenDefined() with an
    // earlier constructor. Always adapt the class the registry actually uses.
    const component = customElements.get(name);
    if (!component || Object.hasOwn(component.prototype, installed)) return;
    const original = component.prototype.updated;
    component.prototype.updated = function(changed) {
      const result = original?.call(this, changed);
      update(this);
      return result;
    };
    Object.defineProperty(component.prototype, installed, {value: true});
    existing(name, update);
  });
}

adapt('cloud-account-overview', overview);
adapt('ha-config-voice-assistants-assistants', assistants);

// Exact native Japanese copy only; preserve Lit anchors and all controls.
const selfHostedCopy = new Map([
  ['Nabu Casa アカウント', 'セルフホスティング Cloud アカウント'],
  ['このページでリモート アクセスを無効にした場合、この設定を有効にすると、Nabu Casa アカウントを介してリモートで再アクティブ化できるようになります。', 'この設定は、ローカルで無効にしたリモートアクセスの外部からの再有効化を許可します。セルフホスティングサーバー側の対応も必要です。'],
  ['Home Assistant Cloud に、暗号化された Home Assistant のバックアップを保存するオプションがあります。この機能は、サブスクリプションに含まれており、追加料金はかかりません。', 'セルフホスティング Cloud に、暗号化された Home Assistant のバックアップを保存できます。保存先と利用可能な容量は、サーバーの構成によって異なります。'],
  ['Nabu Casaは、あなたのデータや暗号化キーにアクセスすることはできません。', 'バックアップの復元に必要な暗号化キーを、安全な場所に保管してください。'],
  ['Home Assistant アプリを設定すると、Webhook が自動的に登録されます。クラウドサブスクリプションをご利用の場合、これは「クラウドフック」となり、Home Assistant Cloud 上でホストされる一意のシークレット URL となります。', 'Home Assistant アプリを設定すると、Webhook が自動的に登録されます。セルフホスティング Cloud のクラウドフックは、サーバー上の一意のシークレット URL を使用します。URL は秘密情報として扱ってください。'],
]);
function providerCopy(host) {
  function visit(node) {
    if (node.nodeType === 3) {
      const replacement = selfHostedCopy.get(node.data.trim());
      if (replacement) node.data = node.data.replace(node.data.trim(), replacement);
    } else if (!['SCRIPT', 'STYLE'].includes(node.nodeName)) {
      for (const child of node.childNodes || []) visit(child);
    }
  }
  if (host.shadowRoot) visit(host.shadowRoot);
}
for (const name of ['cloud-remote-pref', 'cloud-backup-pref', 'cloud-companion-pref']) {
  adapt(name, providerCopy);
}
