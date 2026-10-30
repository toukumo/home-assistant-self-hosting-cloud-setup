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
  const key = host.cloudStatus?.google_registered ? 'alexa_google_google' : 'alexa_google_none';
  text(row.querySelector('[slot="supporting-text"]'),
    localize?.(`ui.panel.config.cloud.account.overview.${key}`));
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
