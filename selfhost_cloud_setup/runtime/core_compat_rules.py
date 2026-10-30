"""Deterministic service-owned Core source transformations; no AI or filesystem I/O.

Rules describe known source contracts. Unknown upstream shapes raise before any
result is returned. Callers stage and validate the complete candidate separately.
"""
from dataclasses import dataclass
import hashlib
import io
import tokenize


@dataclass(frozen=True)
class SourceRule:
    identifier: str
    filename: str
    before: str
    after: str


class UnsupportedSource(SystemExit):
    def __init__(self, identifier):
        self.rule_id = identifier
        super().__init__('Upstream changed: unsupported compatibility rule ' + identifier)


RULESET_VERSION = 1
RULES = (
    SourceRule('cloud-client-import-v1', '__init__.py', 'from .client import CloudClient', 'from .client import CloudClient\nfrom .local_auth import install as install_local_auth\nfrom .provider_ui import async_setup as setup_provider_ui'),
    SourceRule('cloud-provider-install-v1', '__init__.py', '    cloud = hass.data[DATA_CLOUD] = Cloud(client, **kwargs)', '    cloud = hass.data[DATA_CLOUD] = Cloud(client, **kwargs)\n    install_local_auth(cloud)\n    await setup_provider_ui(hass)'),
    SourceRule('cloud-status-privacy-v1', 'http_api.py', '    data = await _account_data(hass, cloud)\n\n    # Only admins are shown the email of a pending registration.', '    data = await _account_data(hass, cloud)\n    if not connection.user.is_admin and "prefs" in data:\n        # Bearer callbacks and secure-device PINs are administrator-only secrets.\n        data["prefs"] = {\n            key: value for key, value in data["prefs"].items()\n            if key not in ("cloudhooks", "google_secure_devices_pin")\n        }\n\n    # Only admins are shown the email of a pending registration.'),
    SourceRule('alexa-relink-entity-sync-v1', 'alexa_config.py',
               '        with suppress(alexa_errors.NoTokenAvailable):\n'
               '            await self._sync_helper(to_update, to_remove)',
               '        with suppress(alexa_errors.NoTokenAvailable, alexa_errors.RequireRelink):\n'
               '            await self._sync_helper(to_update, to_remove)'),
 )



def _starts_as_code(text, start, fragment):
    """Reject canonical examples inside strings without parsing native syntax."""
    ignored = {tokenize.ENCODING, tokenize.INDENT, tokenize.DEDENT,
               tokenize.NL, tokenize.NEWLINE, tokenize.ENDMARKER}
    try:
        expected = next(token for token in tokenize.generate_tokens(io.StringIO(fragment).readline)
                        if token.type not in ignored)
        if expected.type != tokenize.NAME:
            return False
        point = (text.count('\n', 0, start) + 1,
                 start - (text.rfind('\n', 0, start) + 1) + expected.start[1])
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.start > point:
                return False
            if token.start == point and token.type == tokenize.NAME:
                return token.string == expected.string
    except (StopIteration, tokenize.TokenError, SyntaxError):
        return False
    return False


def transform(sources, *, rule_ids=None):
    """Return transformed texts and a public receipt without mutating the input."""
    selected = tuple(rule_ids) if rule_ids is not None else tuple(rule.identifier for rule in RULES)
    known = {rule.identifier: rule for rule in RULES}
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(known):
        raise ValueError('Select known compatibility rules once')
    outputs = dict(sources)
    receipt = {'ruleset_version': RULESET_VERSION, 'applied_rules': [],
               'already_satisfied_rules': [], 'files': {}}
    for identifier in selected:
        rule = known[identifier]
        text = outputs.get(rule.filename)
        already_satisfied=(identifier=='alexa-relink-entity-sync-v1'
                          and isinstance(text,str) and text.count(rule.before)==0
                          and text.count(rule.after)==1)
        anchor=rule.after if already_satisfied else rule.before
        # Canonical contracts occupy complete lines. A prefix match inside an
        # alias, comment or compound statement is an unknown upstream shape.
        if isinstance(text, str) and anchor and text.count(anchor) == 1:
            start = text.index(anchor)
            end = start + len(anchor)
            line_end = text.find('\n', end)
            tail = text[end:line_end if line_end >= 0 else len(text)]
            if ((start and text[start - 1] != '\n') or tail.strip(' \t\r')
                    or not _starts_as_code(text, start, anchor)):
                raise UnsupportedSource(identifier)
        if already_satisfied:
            receipt['already_satisfied_rules'].append(identifier)
            continue
        if not isinstance(text, str) or text.count(rule.before) != 1:
            raise UnsupportedSource(identifier)
        outputs[rule.filename] = text.replace(rule.before, rule.after)
        receipt['applied_rules'].append(identifier)
    for filename in sorted({known[identifier].filename for identifier in selected}):
        receipt['files'][filename] = {
            'before_sha256': hashlib.sha256(sources[filename].encode()).hexdigest(),
            'after_sha256': hashlib.sha256(outputs[filename].encode()).hexdigest(),
        }
    return outputs, receipt
