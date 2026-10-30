"""Reuse RemoteUI's certificate lifecycle with a self-hosted authenticated issuer."""
from pathlib import Path
from contextlib import ExitStack
import asyncio
from datetime import datetime, timezone
import os
import ssl
import tempfile
import stat
import logging

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from hass_nabucasa.acme import AcmeHandler, AcmeClientError, CertificateStatus


def _load_cert_chain(context, certificate, key):
    with ExitStack() as stack:
        sources = []
        for path in (certificate, key):
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Invalid remote certificate file")
            sources.append(f"/dev/fd/{fd}")
        context.load_cert_chain(*sources)


def _same_staging_file(path, original):
    if original is None:
        return False
    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISREG(current.st_mode)
            and (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino))


class SelfHostedCertificateHandler(AcmeHandler):
    # The official provider's ACME account/certificate files remain untouched.
    def _own_path(self, name):
        return Path(self.cloud.path("selfhost-remote")) / name

    @property
    def path_private_key(self):
        return self._own_path("remote_private.pem")

    @property
    def path_fullchain(self):
        return self._own_path("remote_fullchain.pem")

    @property
    def path_account_key(self):
        return self._own_path("acme_account.pem")

    @property
    def path_registration_info(self):
        return self._own_path("acme_reg.json")

    async def reset_acme(self):
        # Native domain changes call this before setting RemoteUI's ready event.
        # Our authenticated CSR issuer has no public ACME account to revoke.
        self._acme_client = None
        self._account_jwk = None
        self._x509 = None

    def _csr(self):
        path = self.path_private_key
        try:
            existing = path.stat()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not stat.S_ISREG(existing.st_mode):
                raise ValueError("Invalid remote private key file")
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError("Invalid remote private key file")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    key = serialization.load_pem_private_key(stream.read(), password=None)
            finally:
                os.close(fd)
        else:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            encoded = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)
            temporary = None
            original_staging = None
            try:
                with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                                 prefix="." + path.name + ".", suffix=".tmp",
                                                 delete=False) as stream:
                    temporary = Path(stream.name)
                    original_staging = os.fstat(stream.fileno())
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                if not _same_staging_file(temporary, original_staging):
                    raise ValueError("Remote private key staging changed")
                os.link(temporary, path)  # Publish without replacing a concurrent key.
            finally:
                if temporary is not None:
                    try:
                        if _same_staging_file(temporary, original_staging):
                            temporary.unlink(missing_ok=True)
                    except OSError:
                        logging.getLogger(__name__).warning("Cannot remove remote private key staging file")
        builder = (x509.CertificateSigningRequestBuilder()
                   .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, self.domains[0])]))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName(name) for name in self.domains]), critical=False))
        return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()

    async def issue_certificate(self):
        try:
            csr = await self.cloud.run_executor(self._csr)
            async with self.cloud.websession.post(f"https://{self.cloud.api_server}/instance/certificate",
                                                 headers={"Authorization": "Bearer " + self.cloud.id_token},
                                                 json={"csr": csr}) as response:
                response.raise_for_status()
                certificate = (await response.json())["certificate"]
            def write():
                if not isinstance(certificate, str):
                    raise ValueError("Invalid remote certificate")
                leaf = x509.load_pem_x509_certificate(certificate.encode())
                common = leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                now = datetime.now(timezone.utc)
                if (len(common) != 1 or common[0].value != self.domains[0]
                        or set(names.get_values_for_type(x509.DNSName)) != set(self.domains)
                        or len(names) != len(set(self.domains))
                        or not leaf.not_valid_before_utc <= now < leaf.not_valid_after_utc):
                    raise ValueError("Invalid remote certificate")
                path = self.path_fullchain
                temporary = None
                original_staging = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent,
                                                     prefix="." + path.name + ".", suffix=".tmp",
                                                     delete=False) as stream:
                        temporary = Path(stream.name)
                        original_staging = os.fstat(stream.fileno())
                        stream.write(certificate)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if not _same_staging_file(temporary, original_staging):
                        raise ValueError("Remote certificate staging changed")
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    _load_cert_chain(context, temporary, self.path_private_key)
                    if not _same_staging_file(temporary, original_staging):
                        raise ValueError("Remote certificate staging changed")
                    temporary.replace(path)
                finally:
                    if temporary is not None:
                        try:
                            if _same_staging_file(temporary, original_staging):
                                temporary.unlink(missing_ok=True)
                        except OSError:
                            logging.getLogger(__name__).warning("Cannot remove remote certificate staging file")
                return leaf
            # Canceling an executor await cannot stop its filesystem writes.
            # Finish this admitted commit before releasing the caller, including
            # repeated cancellation, so the cached leaf matches the disk result.
            commit = asyncio.ensure_future(self.cloud.run_executor(write))
            cancellation = None
            while not commit.done():
                try:
                    await asyncio.shield(commit)
                except asyncio.CancelledError as error:
                    cancellation = error
                except Exception:
                    break  # Observe and propagate the worker result below.
            try:
                self._x509 = commit.result()
                self._update_status(CertificateStatus.LOADED)
            except Exception:
                if cancellation is not None:
                    raise cancellation
                raise
            if cancellation is not None:
                raise cancellation
        except Exception as err:
            raise AcmeClientError("Self-hosted certificate issuance failed") from err
