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
  voiceAvailability(host);
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

// Exact native ko/ja/en copy only; preserve Lit anchors and all controls.
const selfHostedCopy = new Map([
  ["Home Assistant 개발을 후원하면서 최고의 기능을 이용하세요. 어디서든 집에 안전하게 접속하고, 음성 어시스턴트를 추가하고, 외부 위치에 백업을 보관할 수 있습니다. Home Assistant를 만든 팀에서 직접 제공합니다.", "셀프 호스팅 Cloud의 원격 접속, 백업 및 연결 기능을 관리합니다. 사용 가능한 기능은 서버 설정에 따라 달라집니다."],
  ["The best of Home Assistant, while funding its development. Securely reach your home from anywhere, add voice assistants, keep an off-site backup, and more. Built by the team behind Home Assistant.", "Manage remote access, backups and connections for your self-hosted Cloud. Available features depend on your server configuration."],
  ["Home Assistantの優れた機能を最大限に活用しつつ、その開発を支援できます。どこからでも安全に自宅にアクセスしたり、音声アシスタントを追加したり、オフサイトバックアップを作成したりなど、さまざまな機能をご利用いただけます。Home Assistantの開発チームが開発しました。", "セルフホスティング Cloud のリモートアクセス、バックアップ、接続機能を管理します。利用できる機能はサーバーの構成によって異なります。"],

  ["Nabu Casa 계정", "셀프 호스팅 Cloud 계정"],
  ["이 페이지에서 원격 접속을 비활성화해도 이 설정을 켜 두면 Nabu Casa 계정을 통해 원격으로 다시 활성화할 수 있습니다.", "이 페이지에서 원격 접속을 비활성화해도 이 설정을 켜 두면 셀프 호스팅 Cloud 계정을 통해 원격으로 다시 활성화할 수 있습니다. 셀프 호스팅 서버의 지원도 필요합니다."],
  ["암호화된 Home Assistant 백업을 Home Assistant Cloud에 저장할 수 있습니다. 이 기능은 추가 비용 없이 구독에 포함됩니다.", "암호화된 Home Assistant 백업을 셀프 호스팅 Cloud에 저장할 수 있습니다. 저장 위치와 사용 가능한 용량은 서버 설정에 따라 달라집니다."],
  ["Nabu Casa는 데이터나 암호화 키에 액세스할 수 없습니다.", "백업 복원에 필요한 암호화 키를 안전한 곳에 보관하세요."],
  ["Home Assistant 앱을 설정하면 자동으로 웹훅을 등록합니다. 클라우드 구독을 사용하면 이것이 Home Assistant Cloud에서 호스팅되는 고유한 비밀 URL인 클라우드훅이 됩니다.", "Home Assistant 앱을 설정하면 웹훅이 자동 등록됩니다. 셀프 호스팅 Cloud에 연결된 경우 이는 셀프 호스팅 Cloud 서버가 제공하는 고유한 비밀 URL인 클라우드훅이 됩니다. 이 URL은 비밀정보로 보관하세요."],
  ["Nabu Casa account", "Self-hosted Cloud account"],
  ["If you disable remote access on this page, having this setting enabled allows you to reactivate it remotely via your Nabu Casa account.", "If you disable remote access on this page, this setting allows it to be re-enabled remotely through your self-hosted Cloud account. Your self-hosted server must also support this."],
  ["You have the option to store an encrypted Home Assistant backup on Home Assistant Cloud. This is included in your subscription at no extra cost.", "You can store encrypted Home Assistant backups on your self-hosted Cloud. Storage location and available capacity depend on your server configuration."],
  ["Nabu Casa has no access to your data or encryption key.", "Keep the encryption key needed to restore your backups in a safe place."],
  ["When you set up the Home Assistant app, it registers a webhook automatically. With your cloud subscription that becomes a cloudhook, a unique secret URL hosted on Home Assistant Cloud.", "When you set up the Home Assistant app, it registers a webhook automatically. When connected to your self-hosted Cloud, this becomes a cloudhook: a unique secret URL served by your self-hosted Cloud server. Keep this URL private."],
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


const voiceChecks = new WeakMap();
function voiceAvailability(host) {
  const hass = host.hass;
  if (!hass?.callWS || !host.shadowRoot) return;
  let check = voiceChecks.get(host);
  if (!check || check.connection !== hass.connection) {
    check = {connection: hass.connection, result: null};
    voiceChecks.set(host, check);
    hass.callWS({type: 'cloud/selfhost/voice/status'}).then(result => {
      if (voiceChecks.get(host) !== check || host.hass?.connection !== check.connection) return;
      check.result = result;
      host.requestUpdate();
    }).catch(() => {
      if (voiceChecks.get(host) !== check) return;
      check.result = {};
      host.requestUpdate();
    });
  }
  const result = check.result;
  const language = (hass.locale?.language || hass.language || 'en').split('-')[0];
  const messages = {
    en: ['Cloud voice availability has not been verified.', 'Cloud voice is not provided by this server. Configure a separate speech engine in Home Assistant.'],
    ko: ['Cloud 음성 제공 여부를 확인하지 못했습니다.', '이 서버는 Cloud 음성을 제공하지 않습니다. Home Assistant에서 별도 음성 엔진을 설정하세요.'],
    ja: ['Cloud 音声の提供状況を確認できません。', 'このサーバーは Cloud 音声を提供していません。Home Assistant で別の音声エンジンを設定してください。'],
  };
  const available = result?.tts === true;
  const message = (messages[language] || messages.en)[result?.tts === false ? 1 : 0];
  const row = host.shadowRoot.querySelector('ha-list-item-button[href="/config/cloud/voice-assistants"]');
  if (row) {
    const partial = {
      en: ['Cloud speech recognition only', 'Cloud text-to-speech only'],
      ko: ['Cloud 음성 인식만 제공', 'Cloud 음성 출력만 제공'],
      ja: ['Cloud 音声認識のみ提供', 'Cloud 音声合成のみ提供'],
    };
    let summary = message;
    if (result?.tts === true && result?.stt === true) {
      summary = hass.localize?.('ui.panel.config.cloud.account.overview.feature_voice_sub') || ({en: 'Text-to-speech and speech-to-text', ko: '텍스트 음성 변환 및 음성 인식', ja: 'テキスト読み上げと音声認識'}[language] || 'Text-to-speech and speech-to-text');
    } else if (result?.tts === true || result?.stt === true) {
      summary = (partial[language] || partial.en)[result.tts === true ? 1 : 0];
    }
    text(row.querySelector('[slot="supporting-text"]'), summary);
  }
  if (host.localName !== 'cloud-tts-pref') return;
  let notice = host.shadowRoot.querySelector('[data-selfhost-voice-notice]');
  if (!notice) {
    notice = document.createElement('ha-alert');
    notice.setAttribute('data-selfhost-voice-notice', '');
    host.shadowRoot.append(notice);
    style(host.shadowRoot, ':host([data-selfhost-voice-unavailable]) ha-card { display:none!important; } [data-selfhost-voice-notice][hidden] { display:none!important; }');
  }
  host.toggleAttribute('data-selfhost-voice-unavailable', result?.tts === false);
  notice.hidden = available;
  notice.textContent = message;
}
adapt('cloud-tts-pref', voiceAvailability);
