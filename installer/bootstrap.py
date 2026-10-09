#!/usr/bin/env python3
"""Public customer installer: authenticated download of a private release ZIP."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

class InstallerError(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise InstallerError('License endpoint redirected unexpectedly.')


def validated_server(value: str) -> str:
    parsed=urllib.parse.urlsplit(value.strip())
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.port not in (None,443):
        raise InstallerError('License server must be a trusted HTTPS origin without a path or credentials.')
    return parsed.geturl().rstrip('/')


def license_domain(value: str) -> str:
    d=value.strip().lower().rstrip('.')
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+',d) or len(d)>253:
        raise InstallerError('Invalid installation domain.')
    return d


def request_json(server: str, route: str, data: dict, *, stream: bool = False):
    url=server+route
    req=urllib.request.Request(url,data=json.dumps(data).encode('utf-8'),
                               headers={'Content-Type':'application/json','Accept':'application/json' if not stream else 'application/zip'},method='POST')
    opener=urllib.request.build_opener(NoRedirect)
    try:
        return opener.open(req,timeout=120 if stream else 15)
    except urllib.error.HTTPError as exc:
        if exc.code in (400,401,403,404,422):
            raise InstallerError('License rejected or release unavailable.') from None
        raise InstallerError('License server returned HTTP '+str(exc.code)) from None
    except (urllib.error.URLError,TimeoutError) as exc:
        raise InstallerError('Cannot contact the license server over trusted HTTPS.') from exc


def prepare_id(root: Path) -> str:
    root.mkdir(parents=True,exist_ok=True)
    if os.name=='posix':root.chmod(0o700)
    path=root/'install-id'
    if path.exists():
        if path.is_symlink():raise InstallerError('Installation identity file must not be a symlink.')
        identity=path.read_text().strip()
        if not re.fullmatch(r'[0-9a-f]{48}',identity):raise InstallerError('Existing installation ID is corrupt.')
        return identity
    identity=secrets.token_hex(24)
    try:
        with path.open('x',encoding='utf-8') as f:
            f.write(identity+'\n')
    except FileExistsError:
        return prepare_id(root)
    if os.name=='posix':path.chmod(0o600)
    return identity


def verified_download(server: str, payload: dict, checksum: str, output: Path) -> None:
    if not re.fullmatch('[a-f0-9]{64}',checksum):raise InstallerError('Missing trusted release fingerprint.')
    h=hashlib.sha256();total=0
    with request_json(server,'/v1/bundle',payload,stream=True) as response:
        if response.status!=200 or response.headers.get('X-PadsBot-SHA256')!=checksum or response.headers.get('Content-Type','').split(';')[0]!='application/zip':
            raise InstallerError('Release manifest and download metadata do not match.')
        with output.open('xb') as dst:
            while True:
                block=response.read(1024*1024)
                if not block:break
                total+=len(block)
                if total>150*1024*1024:raise InstallerError('Release exceeds size limit.')
                h.update(block);dst.write(block)
    if h.hexdigest()!=checksum:raise InstallerError('Release checksum mismatch. Nothing was installed.')


def extract_release(archive: Path, destination: Path) -> Path:
    if destination.exists():raise InstallerError('Target directory already exists; refusing to overwrite application files.')
    if not zipfile.is_zipfile(archive):raise InstallerError('The downloaded file is not a valid ZIP.')
    tmp=Path(tempfile.mkdtemp(prefix='.padsbot-release-',dir=destination.parent))
    try:
        with zipfile.ZipFile(archive) as z:
            entries=z.infolist()
            names=[];total=0
            for entry in entries:
                p=PurePosixPath(entry.filename)
                if p.is_absolute() or '..' in p.parts or '\\' in entry.filename or entry.flag_bits & 0x1:
                    raise InstallerError('Unsafe release file path or encrypted entry.')
                if (entry.external_attr>>16)&0o170000==stat.S_IFLNK:
                    raise InstallerError('Release contains unsupported symbolic links.')
                total+=entry.file_size
                if total>500*1024*1024:raise InstallerError('Release expands beyond size limit.')
                if p.parts:names.append(p)
            if not names:raise InstallerError('Release archive is empty.')
            root=names[0].parts[0]
            strip=all(p.parts[0]==root for p in names) and all(len(p.parts)>=2 for p in names if p.suffix)
            for entry,p in zip(entries,names):
                parts=p.parts[1:] if strip else p.parts
                if not parts:continue
                target=tmp.joinpath(*parts)
                if entry.is_dir():target.mkdir(parents=True,exist_ok=True);continue
                if any(part in ('.git','AppData','logs') for part in parts) or parts[-1]=='.env':
                    raise InstallerError('Release contains forbidden runtime data.')
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():raise InstallerError('Duplicate paths in release.')
                with z.open(entry) as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
        for name in ('install.py','bot.php','composer.json','requirements-installer.txt'):
            if not (tmp/name).is_file():raise InstallerError('Missing required installation component: '+name)
        tmp.rename(destination)
        return destination
    finally:
        if tmp.exists():shutil.rmtree(tmp)


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description='PadsBot Pro verified customer installer')
    parser.add_argument('--server',default=os.getenv('PADSBOT_LICENSE_URL',''),help='Verified HTTPS license-server origin')
    parser.add_argument('--domain',required=True,help='Licensed bot domain')
    parser.add_argument('--destination',type=Path,default=Path.cwd()/'PadsBot-Pro')
    parser.add_argument('--download-only',action='store_true',help='Extract the verified release but do not execute install.py')
    args=parser.parse_args(argv)
    try:
        server=validated_server(args.server)
        domain=license_domain(args.domain)
        dest=args.destination.resolve()
        dest.parent.mkdir(parents=True,exist_ok=True)
        license_key=getpass.getpass('PadsBot Pro License Key: ').strip()
        if not license_key.startswith('PB2-'):raise InstallerError('Invalid license key format.')
        install_id=prepare_id(dest.parent/'.padsbot-installer')
        payload={'license_key':license_key,'domain':domain,'install_id':install_id}
        with request_json(server,'/v1/activate',payload) as res:
            info=json.loads(res.read(16384))
        release=info.get('release')
        if not info.get('active') or not isinstance(release,dict):
            raise InstallerError('License is valid, but there is no installable release yet.')
        with tempfile.TemporaryDirectory(prefix='padsbot-download-') as temporary:
            zip_path=Path(temporary)/'release.zip'
            verified_download(server,payload,release['sha256'],zip_path)
            extracted=extract_release(zip_path,dest)
        print('Verified release:',release['version'])
        print('Installation files:',extracted)
        if args.download_only:
            print('Verified release extracted successfully.')
            return 0
        print('Starting application setup. Required Python and PHP dependencies must already be available.')
        result=subprocess.run([sys.executable,str(extracted/'install.py')],cwd=extracted,check=False)
        return result.returncode
    except (InstallerError,ValueError,OSError,json.JSONDecodeError) as exc:
        print('ERROR:',exc,file=sys.stderr)
        return 1

if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:
        print('\nInstallation canceled.',file=sys.stderr)
        raise SystemExit(130)
