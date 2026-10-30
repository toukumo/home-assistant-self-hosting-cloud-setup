"""Local auth transport for the existing CognitoAuth lifecycle, lab only.

Native Cloud UI and hass-nabucasa connection/refresh logic remain in use.
TLS and RS256 validation are required; official Cloud credentials are never used.
"""
from contextlib import contextmanager
import os
import time
import ssl
import json
import functools
import stat
from pathlib import Path

import jwt
import aiohttp
import urllib.request
import urllib.error
from botocore.exceptions import ClientError
from hass_nabucasa.voice_data import TTS_VOICES
from hass_nabucasa.voice import STT_LANGUAGES


def _regular_path(value):
    path = Path(value)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Invalid local authentication file")
    return path


@contextmanager
def _regular_file(value):
    path = _regular_path(value)
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Invalid local authentication file")
        yield fd
    finally:
        os.close(fd)


def _read_regular(value, *, binary=False):
    with _regular_file(value) as fd:
        with os.fdopen(fd, "rb" if binary else "r", closefd=False) as stream:
            return stream.read()


if os.environ.get("SELFHOST_CLOUD_URL"):
    _SETTINGS = {"url": os.environ["SELFHOST_CLOUD_URL"], "ca": os.environ["SELFHOST_CLOUD_CA"],
                 "jwt_public": os.environ["SELFHOST_CLOUD_JWT_PUBLIC"]}
else:
    _SETTINGS = json.loads(_read_regular(Path(__file__).with_name("selfhost.json")))
_TLS = ssl.create_default_context()
with _regular_file(_SETTINGS["ca"]) as _ca_fd:
    _TLS.load_verify_locations(cafile=f"/dev/fd/{_ca_fd}")
# Preserve the native control layout while naming the actual local voices.
TTS_VOICES.clear()
TTS_VOICES.update({"ko-KR": {"SunHiNeural": "Piper KSS (local)"},
                   "ja-JP": {"NanamiNeural": "Piper Hi-Fi Captain (local)"}})
STT_LANGUAGES[:] = ["ko-KR", "ja-JP"]


class LocalCognito:
    def __init__(self, cloud, username=None, access_token=None, refresh_token=None, **kwargs):
        self.cloud = cloud
        self.username = username
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.id_token = None
        self.url = _SETTINGS["url"].rstrip("/")
        self.ca = _SETTINGS["ca"]
        self.public_key = _read_regular(_SETTINGS["jwt_public"], binary=True)
        if self.url != f"https://{cloud.api_server}":
            raise ValueError("Local authentication URL must match configured Cloud API")

    def _request(self, path, data):
        request = urllib.request.Request(self.url + path, data=json.dumps(data).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, context=_TLS, timeout=15) as response:
                values = json.load(response)
        except urllib.error.HTTPError as err:
            # urlopen raises before entering the response context on HTTP errors.
            # Release its response stream before translating or propagating it.
            err.close()
            if err.code in (401, 403):
                raise ClientError({"Error": {"Code": "NotAuthorizedException", "Message": "Invalid local credentials"}}, "LocalAuth") from None
            raise
        validated = {}
        for key in ("id_token", "access_token"):
            claims = jwt.decode(values[key], self.public_key, algorithms=["RS256"],
                                issuer=self.url, audience="ha-cloud-clone",
                                options={"require": ["exp"] if key == "access_token" else []})
            expected = "id" if key == "id_token" else "access"
            if claims["token_use"] != expected:
                raise ValueError("Incorrect local token type")
            validated[key] = values[key]
        refresh_token = values.get("refresh_token", self.refresh_token)
        if "refresh_token" in values and (not isinstance(refresh_token, str) or not refresh_token):
            raise ValueError("Invalid local refresh token")
        self.id_token, self.access_token, self.refresh_token = (
            validated["id_token"], validated["access_token"], refresh_token)

    def initiate_forgot_password(self):
        request = urllib.request.Request(self.url + "/auth/password/request", data=json.dumps({"email": self.username}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, context=_TLS, timeout=15) as response:
                response.read()
        except urllib.error.HTTPError as err:
            err.close()
            raise

    def authenticate(self, password):
        self._request("/auth/login", {"email": self.username, "password": password})

    def renew_access_token(self):
        self._request("/auth/refresh", {"refresh_token": self.refresh_token})

    def check_token(self, renew=False):
        # Match pycognito: True means the access token needs renewal.
        try:
            claims = jwt.decode(self.access_token, self.public_key, algorithms=["RS256"],
                                issuer=self.url, audience="ha-cloud-clone", options={"require": ["exp"]})
            return int(claims["exp"]) <= time.time() + 60
        except jwt.ExpiredSignatureError:
            return True


def install(cloud):
    if cloud.mode != "development":
        raise ValueError("Self-hosted auth is restricted to explicit development mode")
    cloud.auth._create_cognito_client = lambda **kwargs: LocalCognito(cloud, **kwargs)
    if url := _SETTINGS.get("snitun_websocket"):
        import hass_nabucasa.remote as remote
        from .snitun_transport import client_factory
        remote.SniTunClientAioHttp = client_factory(cloud, url)
    if _SETTINGS.get("certificate_issuer"):
        import hass_nabucasa.remote as remote
        from .remote_certificate import SelfHostedCertificateHandler
        remote.AcmeHandler = SelfHostedCertificateHandler
    if config := _SETTINGS.get("local_voice"):
        from .voice_relay import install as install_voice
        install_voice(config)
    # HA's shared session uses its own CA store. Keep this trust addition scoped
    # to Cloud rather than changing trust or certificate verification globally.
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_TLS))
    session.ws_connect = functools.partial(session.ws_connect, max_msg_size=16 * 1024**2)
    cloud.client._websession = session
    from .google_report_recovery import install as install_google_recovery
    remove_google_recovery = install_google_recovery(cloud.client._hass, cloud)
    async def shutdown(event):
        remove_google_recovery()
        await session.close()
    cloud.client._hass.bus.async_listen_once("homeassistant_stop", shutdown)
    original_connected = cloud.client.cloud_connected
    from .provider_migration import ProviderMigration, validate_generated_hook, filter_hook_registration
    original_generate = cloud.cloudhooks.generate
    async def generate_hook():
        return validate_generated_hook(await original_generate(), _SETTINGS["url"])
    cloud.cloudhooks.generate = generate_hook
    original_send = cloud.iot.async_send_message
    async def send_message(handler, payload, expect_answer=True):
        if handler == "webhook-register":
            payload = filter_hook_registration(payload, cloud.client.prefs.cloudhooks, _SETTINGS["url"])
        return await original_send(handler, payload, expect_answer=expect_answer)
    cloud.iot.async_send_message = send_message
    migration = None
    if _SETTINGS.get("migrate_existing_account"):
        migration = ProviderMigration(cloud.client.prefs, cloud.client._hass.config.path(".selfhost_cloud_migration"))
    async def connected():
        # Unsupported features must not appear enabled or start retry loops.
        current = cloud.client.prefs._prefs.get("username")
        if not migration and current and current != cloud.username:
            raise ValueError("Existing provider preferences require explicit migration; no reset allowed")
        if migration:
            await migration.prepare(cloud.username, _SETTINGS["url"], cloud.remote.instance_domain)
        await cloud.client.prefs.async_set_username(cloud.username)
        if migration:
            await migration.restore_hooks(cloud)
        services = await cloud.account.services()
        updates = {}
        for service, pref in (("alexa", "alexa_enabled"), ("google_home", "google_enabled"), ("webrtc", "cloud_ice_servers_enabled")):
            if not services[service]["available"]:
                updates[pref] = False
        if not migration and cloud.client.prefs.tts_default_voice[0] not in TTS_VOICES:
            updates["tts_default_voice"] = ("ko-KR", "SunHiNeural")
        if updates:
            await cloud.client.prefs.async_update(**updates)
        await original_connected()
    cloud.client.cloud_connected = connected
