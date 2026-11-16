"""User-initiated public GitHub release discovery and bounded verified staging."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from version import PRODUCT_VERSION
from release_config import REPOSITORY

ARCHIVE='Qchip-TEC-Windows-x64.zip'
MANIFEST='update.json'
PROGRAM_FILES={'Qchip TEC.exe','version.json','LICENSE.txt','THIRD_PARTY_NOTICES.txt'}
HOSTS={'api.github.com','github.com','objects.githubusercontent.com','release-assets.githubusercontent.com'}
MAX_ARCHIVE=180*1024*1024
MAX_FILE=120*1024*1024


def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',value):
        raise ValueError('无效的更新版本')
    return tuple(map(int,value.split('.')))


def github_url(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme!='https' or p.hostname not in HOSTS or p.port not in (None,443) or p.username or p.password or p.fragment:
        raise ValueError('更新下载地址不受信任')
    return url


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return super().redirect_request(req,fp,code,msg,headers,github_url(newurl))


def fetch(url,limit,destination=None):
    req=urllib.request.Request(github_url(url),headers={'User-Agent':'QchipTEC/'+PRODUCT_VERSION,
          'Accept':'application/vnd.github+json' if '/releases/latest' in url else 'application/octet-stream'})
    opener=urllib.request.build_opener(Redirects())
    with opener.open(req,timeout=20) as response:
        github_url(response.geturl())
        length=response.headers.get('Content-Length')
        if length and (not length.isdigit() or int(length)>limit):raise ValueError('更新文件超过大小限制')
        count=0;data=bytearray();digest=hashlib.sha256()
        stream=Path(destination).open('xb') if destination else None
        try:
            while True:
                chunk=response.read(min(65536,limit-count+1))
                if not chunk:break
                count+=len(chunk)
                if count>limit:raise ValueError('更新文件超过大小限制')
                digest.update(chunk)
                if stream:stream.write(chunk)
                else:data.extend(chunk)
            if stream:stream.flush();os.fsync(stream.fileno())
        finally:
            if stream:stream.close()
    return (count,digest.hexdigest()) if destination else bytes(data)


def asset_url(asset,repository,tag,name):
    if asset.get('name')!=name or asset.get('state')!='uploaded':raise ValueError('更新附件尚未就绪')
    expected='https://github.com/{}/releases/download/{}/{}'.format(repository,urllib.parse.quote(tag,safe=''),urllib.parse.quote(name,safe=''))
    if asset.get('browser_download_url')!=expected:raise ValueError('更新附件来源不匹配')
    return github_url(expected)


def validate_manifest(m,release_version):
    if not isinstance(m,dict) or set(m)!={'schema','product','version','platform','archive','files'}:
        raise ValueError('更新清单格式无效')
    if m['schema']!=1 or m['product']!='QchipTEC' or m['platform']!='windows-x64' or m['version']!=release_version:
        raise ValueError('更新产品或版本不匹配')
    version(m['version'])
    archive=m['archive']
    if not isinstance(archive,dict) or set(archive)!={'name','bytes','sha256'} or archive['name']!=ARCHIVE:
        raise ValueError('更新包名称无效')
    validate_hash_size(archive,MAX_ARCHIVE)
    files=m['files']
    if not isinstance(files,list) or not 2<=len(files)<=len(PROGRAM_FILES):raise ValueError('更新文件清单无效')
    names=[]
    for f in files:
        if not isinstance(f,dict) or set(f)!={'path','bytes','sha256'} or f['path'] not in PROGRAM_FILES:
            raise ValueError('更新包含非程序文件')
        validate_hash_size(f,MAX_FILE);names.append(f['path'])
    if len(names)!=len(set(names)) or not {'Qchip TEC.exe','version.json'}<=set(names):
        raise ValueError('更新程序文件不完整')
    return m


def validate_hash_size(value,limit):
    if type(value.get('bytes')) is not int or not 0<value['bytes']<=limit or not re.fullmatch(r'[0-9a-f]{64}',str(value.get('sha256',''))):
        raise ValueError('更新文件校验信息无效')


def extract_verified(archive,folder,manifest):
    folder=Path(folder);folder.mkdir()
    expected={f['path']:f for f in manifest['files']}
    with zipfile.ZipFile(archive) as z:
        entries=z.infolist()
        if len(entries)!=len(expected) or {e.filename for e in entries}!=set(expected):
            raise ValueError('更新包文件清单不匹配')
        for entry in entries:
            f=expected[entry.filename]
            mode=entry.external_attr>>16
            if entry.is_dir() or mode&0o170000==0o120000 or entry.flag_bits&1 or entry.file_size!=f['bytes']:
                raise ValueError('更新包文件类型或大小无效')
            target=folder/entry.filename
            digest=hashlib.sha256();count=0
            with z.open(entry) as src,target.open('xb') as out:
                while True:
                    chunk=src.read(65536)
                    if not chunk:break
                    count+=len(chunk)
                    if count>f['bytes']:raise ValueError('更新文件超出清单大小')
                    digest.update(chunk);out.write(chunk)
            if count!=f['bytes'] or digest.hexdigest()!=f['sha256']:raise ValueError('更新文件哈希不匹配')
    metadata=json.loads((folder/'version.json').read_text(encoding='utf-8'))
    if metadata!={'product':'QchipTEC','version':manifest['version']}:
        raise ValueError('更新包内部版本不匹配')


class Updates:
    def __init__(self,directory,repository=REPOSITORY,transport=fetch,current=PRODUCT_VERSION,install=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):raise ValueError('无效更新仓库')
        self.root=Path(directory).resolve();self.repository=repository;self.transport=transport
        self.current=current;self.install=install;self.lock=threading.Lock();self.release=None;self.ready=None
        self.value=dict(state='idle',current=current,available=None,message='',can_install=False)

    def snapshot(self):
        with self.lock:return dict(self.value)

    def _set(self,**values):
        with self.lock:self.value.update(values)

    def check(self):
        self.ready=None;self.release=None
        self._set(state='checking',message='正在检查更新…',can_install=False)
        try:
            raw=self.transport('https://api.github.com/repos/'+self.repository+'/releases/latest',1024*1024)
            release=json.loads(raw)
            if release.get('draft') or release.get('prerelease'):raise ValueError('没有可用的正式版本')
            tag=release.get('tag_name','');available=tag[1:] if tag.startswith('v') else tag
            if version(available)<=version(self.current):
                return self._set(state='current',available=available,message='已是最新版本')
            assets=release.get('assets',[])
            names={a.get('name') for a in assets if a.get('state')=='uploaded'}
            if not {MANIFEST,ARCHIVE}<=names:
                return self._set(state='building',available=available,message='新版本构建中，请稍后重试')
            if sum(a.get('name') in (MANIFEST,ARCHIVE) for a in assets)!=2:raise ValueError('更新附件重复')
            self.release=(release,available)
            self._set(state='available',available=available,message='发现新版本 '+available)
        except urllib.error.HTTPError as e:
            self._set(state='unpublished' if e.code==404 else 'error',message='尚未发布更新' if e.code==404 else '检查更新失败，请稍后重试')
        except Exception:
            self._set(state='error',message='检查更新失败，请稍后重试')

    def download(self):
        if not self.release:raise ValueError('请先检查更新')
        self._set(state='downloading',message='正在下载并校验…',can_install=False)
        stage=None
        try:
            release,available=self.release
            assets={a['name']:a for a in release['assets']}
            tag=release['tag_name']
            raw=self.transport(asset_url(assets[MANIFEST],self.repository,tag,MANIFEST),128*1024)
            expected_digest=assets[MANIFEST].get('digest')
            if expected_digest and expected_digest!='sha256:'+hashlib.sha256(raw).hexdigest():raise ValueError('更新清单哈希错误')
            m=validate_manifest(json.loads(raw),available)
            if assets[ARCHIVE].get('size')!=m['archive']['bytes']:raise ValueError('更新包大小不匹配')
            self.root.mkdir(parents=True,exist_ok=True)
            stage=self.root/secrets.token_hex(16);stage.mkdir()
            archive=stage/ARCHIVE
            size,sha=self.transport(asset_url(assets[ARCHIVE],self.repository,tag,ARCHIVE),MAX_ARCHIVE,archive)
            if size!=m['archive']['bytes'] or sha!=m['archive']['sha256']:raise ValueError('更新包哈希错误')
            extract_verified(archive,stage/'program',m)
            self.ready=dict(directory=str(stage/'program'),manifest=m)
            self._set(state='ready',message='下载完成。更新并重启将正常结束本次采集与日志。',can_install=self.install is not None)
        except Exception:
            # Remove only files created inside our unique, checked staging directory.
            if stage and stage.resolve().parent==self.root and not stage.is_symlink() and re.fullmatch('[0-9a-f]{32}',stage.name):
                shutil.rmtree(str(stage),ignore_errors=True)
            self.ready=None;self._set(state='error',message='下载或校验失败，当前程序未改变',can_install=False)

    def launch(self,action):
        if action not in ('check','download','install'):raise ValueError('无效更新操作')
        with self.lock:
            if self.value['state'] in ('checking','downloading','installing'):raise ValueError('更新正在处理中')
            if action=='download' and (not self.release or self.value['state']!='available'):
                raise ValueError('请先检查并选择可用更新')
            if action=='install':
                if not self.ready or not self.install or self.value['state']!='ready':raise ValueError('更新尚未准备好，或正在附着独立服务')
                self.value.update(state='installing',can_install=False,message='正在正常退出并更新…')
            else:self.value['state']='checking' if action=='check' else 'downloading'
        def run():
            try:
                if action=='install':self.install(self.ready)
                elif action=='check':self.check()
                else:self.download()
            except Exception:self._set(state='error',message='更新操作未完成，当前程序未改变',can_install=False)
        threading.Thread(target=run,name='Qchip updates',daemon=True).start()
