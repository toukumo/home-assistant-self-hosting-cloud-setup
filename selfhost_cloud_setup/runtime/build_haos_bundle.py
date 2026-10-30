"""Build a version-pinned custom Cloud integration; never installs on live HA."""
import json
import io
import tokenize
import keyword
import ctypes
import errno
import os
import sys
from pathlib import Path
import shutil
import argparse
import re
import tempfile

from core_compat_rules import RULES, transform

BASE = Path(__file__).parent


def _read_source_text(path):
    with Path(path).open('r',encoding='utf-8',newline='') as stream:
        return stream.read()


def _write_source_text(path,text):
    with Path(path).open('w',encoding='utf-8',newline='') as stream:
        stream.write(text)

# Owner-defined service additions, independent of the replaceable compiler's
# rule table and receipt. Inverting these is a preservation check, not a proof
# of complete runtime behavior or arbitrary future Core compatibility.
_SERVICE_INSERTIONS = {
    '__init__.py': (
        '\nfrom .local_auth import install as install_local_auth\nfrom .provider_ui import async_setup as setup_provider_ui',
        '\n    install_local_auth(cloud)\n    await setup_provider_ui(hass)',
    ),
    'http_api.py': (
        '\n    if not connection.user.is_admin and "prefs" in data:\n'
        '        # Bearer callbacks and secure-device PINs are administrator-only secrets.\n'
        '        data["prefs"] = {\n'
        '            key: value for key, value in data["prefs"].items()\n'
        '            if key not in ("cloudhooks", "google_secure_devices_pin")\n'
        '        }',
    ),
    'alexa_config.py': (
        ', alexa_errors.RequireRelink',
    ),
}


def _insertion_has_code_anchor(text,offset,name,index):
    """Check real preceding tokens without parsing newer target Core grammar."""
    start=text.rfind('\n',0,offset)+1;line=text[start:offset]
    indent=len(line)-len(line.lstrip(' \t'));row=text.count('\n',0,start)+1
    expected_indent=(0 if name=='__init__.py' and index==0 else
                     8 if name=='alexa_config.py' else 4)
    if line[:indent]!=' '*expected_indent:return False
    ignored={tokenize.INDENT,tokenize.DEDENT,tokenize.NL,tokenize.NEWLINE,tokenize.ENDMARKER}
    parts=[];closed=False
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.start[0]>row:break
            if token.start[0]==row and token.type==tokenize.NEWLINE:closed=True
            if token.start[0]!=row or token.type in ignored:continue
            if not parts and (token.start!=(row,indent) or token.type!=tokenize.NAME):return False
            if token.end[0]!=row or token.type==tokenize.COMMENT:return False
            parts.append(token.string)
    except (tokenize.TokenError,SyntaxError,IndentationError):return False
    return closed and _code_anchor_matches(parts,name,index)


def _code_anchor_matches(parts,name,index):
    if name=='alexa_config.py':
        return parts in (
            ['with','suppress','(','alexa_errors','.','NoTokenAvailable',')',':'],
            ['with','suppress','(','alexa_errors','.','NoTokenAvailable',',',
             'alexa_errors','.','RequireRelink',')',':'],
        )
    if name=='__init__.py' and index==0:
        identifier=r'[A-Za-z_][A-Za-z0-9_]*'
        match=re.fullmatch(r'from \. (?P<module>'+identifier+r'(?: \. '+identifier+r'){0,3}) import CloudClient(?: as (?P<alias>'+identifier+r'))?',' '.join(parts))
        if match is None:return False
        alias=match['alias']
        return (not any(keyword.iskeyword(part) for part in match['module'].split(' . '))
                and (alias is None or (not keyword.iskeyword(alias) and alias!='__debug__')))
    if name=='__init__.py':
        return parts in (
            ['cloud','=','hass','.','data','[','DATA_CLOUD',']','=','Cloud','(','client',',','**','kwargs',')'],
            ['hass','.','data','[','DATA_CLOUD',']','=','cloud'],
        )
    return parts==['data','=','await','_account_data','(','hass',',','cloud',')']


def _native_code_anchor_positions(text,name,index):
    """Collect matching physical code lines with one forward token stream."""
    expected_indent=(0 if name=='__init__.py' and index==0 else
                     8 if name=='alexa_config.py' else 4)
    candidates=[];offset=0
    for row,line in enumerate(text.split('\n'),1):
        indent=len(line)-len(line.lstrip(' \t'))
        if line[:indent]==' '*expected_indent and re.match(r'[ \t]*(?:from|cloud|hass|data|with)\b',line):
            candidates.append((row,indent,offset+len(line.rstrip('\r'))))
        offset+=len(line)+1
    positions=[];cursor=0;parts=[];valid=True;closed=False
    ignored={tokenize.INDENT,tokenize.DEDENT,tokenize.NL,tokenize.NEWLINE,tokenize.ENDMARKER}
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            while cursor<len(candidates) and candidates[cursor][0]<token.start[0]:
                if valid and closed and parts and _code_anchor_matches(parts,name,index):positions.append(candidates[cursor][2])
                cursor+=1;parts=[];valid=True;closed=False
            if cursor==len(candidates):continue
            row,indent,_=candidates[cursor]
            if token.start[0]==row and token.type==tokenize.NEWLINE:closed=True
            if token.start[0]!=row or token.type in ignored:continue
            if not parts and (token.start!=(row,indent) or token.type!=tokenize.NAME):valid=False
            if token.end[0]!=row or token.type==tokenize.COMMENT:valid=False
            parts.append(token.string)
    except (tokenize.TokenError,SyntaxError,IndentationError):
        raise ValueError('Unable to tokenize native source') from None
    return positions


def _native_token_analysis(text):
    """Normalize only the owner-known single CloudClient import spelling."""
    ignored={tokenize.ENCODING,tokenize.ENDMARKER,tokenize.NL}
    groups=[];current=[]
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type in ignored:continue
        current.append((token.type,token.string))
        if token.type==tokenize.NEWLINE:
            groups.append(current);current=[]
    if current:groups.append(current)
    canonical=((tokenize.NAME,'from'),(tokenize.OP,'.'),(tokenize.NAME,'client'),
               (tokenize.NAME,'import'),(tokenize.NAME,'CloudClient'),
               (tokenize.NEWLINE,'\n'))
    parenthesized=((tokenize.NAME,'from'),(tokenize.OP,'.'),(tokenize.NAME,'client'),
                   (tokenize.NAME,'import'),(tokenize.OP,'('),(tokenize.NAME,'CloudClient'),
                   (tokenize.OP,','),(tokenize.OP,')'),(tokenize.NEWLINE,'\n'))
    recognized=sum(tuple(group) in (canonical,parenthesized) for group in groups)
    # The transforming rule accepts one unique import anchor. Refuse ambiguous
    # source instead of using normalization to authorize multiple changes.
    if recognized>1:raise ValueError('Ambiguous native CloudClient import')
    return (tuple(item for group in groups for item in
                  (canonical if tuple(group)==parenthesized else group)),
            recognized,sum(tuple(group)==parenthesized for group in groups))


def _verify_native_preserved(sources, transformed):
    """Require native tokens after removing only approved additions.

    A service-owned rule may recognize a whitespace-only spelling of an
    anchor. Permit that formatting difference only when Python's tokenizer
    proves the complete native source has the same token stream.
    """
    error='Native source changed outside approved service insertions'
    if (not isinstance(transformed,dict) or set(transformed)!=set(sources)
            or not sources or not set(sources)<=set(_SERVICE_INSERTIONS)):
        raise ValueError(error)
    for name, original in sources.items():
        restored=transformed[name]
        if not isinstance(restored,str):raise ValueError(error)
        for index,addition in enumerate(_SERVICE_INSERTIONS[name]):
            if name=='alexa_config.py':
                satisfied=('        with suppress(alexa_errors.NoTokenAvailable, alexa_errors.RequireRelink):\n'
                           '            await self._sync_helper(to_update, to_remove)')
                if restored==original and original.count(satisfied)==1:
                    if len(_native_code_anchor_positions(original,name,index))!=1:raise ValueError(error)
                    continue
                if restored.count(addition)!=original.count(addition)+1:raise ValueError(error)
            elif addition in original or restored.count(addition)!=1:
                raise ValueError(error)
            # Ambiguous real anchors cannot establish which native function owns
            # this addition. Refuse rather than trusting the replacement compiler.
            anchors=_native_code_anchor_positions(original,name,index)
            if len(anchors)!=1:
                _,_,parenthesized=_native_token_analysis(original)
                if not (name=='__init__.py' and index==0 and parenthesized==1):
                    raise ValueError(error)
            offsets=[];cursor=0
            while True:
                cursor=restored.find(addition,cursor)
                if cursor<0:break
                if _insertion_has_code_anchor(restored,cursor,name,index):offsets.append(cursor)
                cursor+=len(addition)
            if len(offsets)!=1:raise ValueError(error)
            offset=offsets[0]
            restored=restored[:offset]+restored[offset+len(addition):]
        if restored!=original:
            try:
                if (_native_token_analysis(restored)[0]!=_native_token_analysis(original)[0]):
                    raise ValueError(error)
            except (tokenize.TokenError,SyntaxError,IndentationError):
                raise ValueError(error) from None


def _publish_directory(source, destination):
    """Move a complete sibling tree without replacing any existing name."""
    source_bytes, destination_bytes = os.fsencode(source), os.fsencode(destination)
    if b'\0' in source_bytes or b'\0' in destination_bytes:
        raise ValueError("Embedded null byte in candidate path")
    if sys.platform == "win32":
        os.rename(source, destination)  # Windows refuses an existing destination.
        return
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "linux":
        rename = getattr(library, "renameat2", None)
        arguments = (-100, source_bytes, -100, destination_bytes, 1)  # AT_FDCWD, RENAME_NOREPLACE
        signature = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    elif sys.platform == "darwin":
        rename = getattr(library, "renamex_np", None)
        arguments = (source_bytes, destination_bytes, 4)  # RENAME_EXCL
        signature = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    else:
        rename = None
    if rename is None:
        raise OSError(errno.ENOTSUP, "Exclusive candidate directory publication is unavailable")
    rename.argtypes = signature
    rename.restype = ctypes.c_int
    if rename(*arguments):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), os.fspath(destination))


def build(version, source, destination):
    if not re.fullmatch(r"20\d{2}\.\d{1,2}\.\d+", version):
        raise ValueError("A concrete stable Core version is required")
    if source.is_symlink():
        raise ValueError("Native source links are refused")
    if not source.is_dir():
        raise SystemExit("Extract native cloud integration from the pinned HA image first")
    # Validate all patch anchors in a temporary tree before publishing a bundle.
    with tempfile.TemporaryDirectory() as temporary:
        dest = Path(temporary) / "cloud"
        _populate(source, dest)
        (dest / "provider-account-ui.js").unlink(missing_ok=True)
        # Publishing must never merge into an old bundle or provider runtime.
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Output already exists; choose a new candidate directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".cloud-build-", dir=destination.parent) as staging:
            complete = Path(staging) / "cloud"
            shutil.copytree(dest, complete)
            _publish_directory(complete, destination)
    print(f"Built candidate custom_components/cloud for HA {version}; validation still required")


def _protect_cloud_status(path):
    """Keep the existing public helper while delegating to the service rule."""
    if not path.is_file():
        raise SystemExit("Upstream changed: missing Cloud status implementation")
    sources={'http_api.py': _read_source_text(path)}
    transformed, _ = transform(dict(sources),rule_ids=('cloud-status-privacy-v1',))
    _verify_native_preserved(sources,transformed)
    _write_source_text(path,transformed['http_api.py'])


def _populate(SOURCE, DEST):
    excluded = shutil.ignore_patterns("__pycache__", "*.pyc", "selfhost.json", "smtp.json", "account.json")
    def ignore(directory, names):
        ignored = excluded(directory, names)
        if any((Path(directory) / name).is_symlink() for name in names if name not in ignored):
            raise ValueError("Native source links are refused")
        return ignored
    shutil.copytree(SOURCE, DEST, ignore=ignore)
    # Prepare every source transformation before writing any transformed file.
    names = ('__init__.py', 'http_api.py', 'alexa_config.py')
    if not (DEST/'alexa_config.py').is_file():
        raise SystemExit('Upstream changed: missing Alexa Cloud compatibility source')
    sources = {name: _read_source_text(DEST / name) for name in names if (DEST / name).is_file()}
    selected=tuple(rule.identifier for rule in RULES if rule.filename in sources)
    transformed, _ = transform(dict(sources),rule_ids=selected)
    _verify_native_preserved(sources,transformed)
    for name in sources:
        _write_source_text(DEST / name,transformed[name])
    if not any(path.is_file() for path in (DEST / "translations").glob("*.json")):
        raise ValueError("Native runtime translations are required; extract Cloud from the pinned Core image")
    shutil.copyfile(BASE / "google_report_recovery.py", DEST / "google_report_recovery.py")
    for name in ("provider_recovery.py", "password-recovery-ui.js", "cloud-i18n.js"):
        shutil.copyfile(BASE / name, DEST / name)
    shutil.copyfile(BASE / "local_auth.py", DEST / "local_auth.py")
    shutil.copyfile(BASE / "provider_migration.py", DEST / "provider_migration.py")
    shutil.copyfile(BASE / "snitun_transport.py", DEST / "snitun_transport.py")
    shutil.copyfile(BASE / "remote_certificate.py", DEST / "remote_certificate.py")
    shutil.copyfile(BASE / "voice_bridge.py", DEST / "voice_bridge.py")
    shutil.copyfile(BASE / "voice_relay.py", DEST / "voice_relay.py")
    shutil.copyfile(BASE / "provider_ui.py", DEST / "provider_ui.py")
    shutil.copyfile(BASE / "service_core_public_status.py", DEST / "service_core_public_status.py")
    shutil.copyfile(BASE / "selfhost-dashboard.js", DEST / "selfhost-dashboard.js")
    shutil.copyfile(BASE / "cloud-route-adapter.js", DEST / "cloud-route-adapter.js")
    shutil.copyfile(BASE / "deployment/platforms/home-assistant-selfhost-cloud-512.png", DEST / "provider-icon.png")
    (DEST / "selfhost.example.json").write_text(json.dumps({"url": "https://cloud.test:8443", "ca": "/config/selfhost/ca.pem",
                                                  "jwt_public": "/config/selfhost/jwt-public.pem",
                                                  "snitun_websocket": "wss://cloud.test:8443/snitun",
                                                  "certificate_issuer": True,
                                                  "migrate_existing_account": False}, indent=2) + "\n")
    (DEST / "BUNDLE_README.md").write_text(
        "# Code-only Cloud integration candidate\n\n"
        "Keep the existing private selfhost.json and recovery material when updating code.\n"
        "selfhost.example.json contains lab placeholders only; it is not an operating configuration.\n"
        "Account migration is disabled in the example. Do not replace existing provider settings.\n"
        "Generation is not deployment or a completed Core runtime compatibility gate.\n"
    )
    manifest_path = DEST / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = "0.3.2"
    manifest["codeowners"] = []
    if "frontend" not in manifest["dependencies"]:
        manifest["dependencies"].append("frontend")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.copyfile(BASE / "licenses/Home-Assistant-Core-Apache-2.0.txt", DEST / "LICENSE.md")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Explicit stable Core version matching the selected native source")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, help="New candidate directory (must not already exist)")
    args = parser.parse_args()
    try:
        build(args.version, args.source or BASE / ("vendor/core-cloud-" + args.version),
              args.output or BASE / "dist/haos-candidates" / args.version / "custom_components/cloud")
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
