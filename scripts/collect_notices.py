"""Collect installed locked dependency licenses, never user files or secrets."""
import importlib.metadata as md
import json
from pathlib import Path
import sys


def collect(root,node_modules):
    root=Path(root);out=root/'docs/third_party';out.mkdir(parents=True,exist_ok=True)
    inventory=[];texts=[]
    names=[line.split('==')[0] for line in (root/'requirements-build.lock').read_text().splitlines() if '==' in line]
    for name in names:
        dist=md.distribution(name);m=dist.metadata
        files=[]
        for f in dist.files or ():
            low=str(f).lower()
            if '.dist-info/' in low and any(x in Path(low).name for x in ('license','copying','notice')):
                p=Path(dist.locate_file(f))
                if p.is_file() and p.stat().st_size<400000:
                    text=p.read_text(encoding='utf-8',errors='replace');files.append(str(f));texts.append('\n===== '+name+' '+dist.version+' / '+Path(f).name+' =====\n'+text)
        license=m.get('License-Expression') or m.get('License') or 'See included license text'
        if len(license)>150:license='See included license text'
        inventory.append(dict(name=name,version=dist.version,license=license,
                              source=m.get_all('Project-URL') or [m.get('Home-page','')],license_files=files))
    for name in ('react','react-dom','scheduler','lucide-react','esbuild','@esbuild/win32-x64'):
        pkg=Path(node_modules)/name;meta=json.loads((pkg/'package.json').read_text())
        ls=[p for p in pkg.iterdir() if p.is_file() and p.name.lower().startswith(('license','notice','copying'))]
        for p in ls:texts.append('\n===== '+name+' '+meta['version']+' / '+p.name+' =====\n'+p.read_text(encoding='utf-8'))
        inventory.append(dict(name=name,version=meta['version'],license=meta.get('license','See text'),source=meta.get('repository'),license_files=[p.name for p in ls]))
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if not python_license.exists():raise ValueError('CPython license missing')
    texts.append('\n===== CPython '+sys.version.split()[0]+' =====\n'+python_license.read_text(encoding='utf-8'))
    supplements=out/'supplements'
    if supplements.exists():
        for p in sorted(supplements.glob('*.txt')):
            texts.append('\n===== Upstream supplement / '+p.name+' =====\n'+p.read_text(encoding='utf-8'))
        for e in inventory:
            normalized=e['name'].replace('_','-').lower()
            matching=list(supplements.glob(normalized+'-'+e['version']+'-LICENSE.txt'))
            if matching:e['license_files'] += ['supplements/'+p.name for p in matching]
            if normalized=='proxy-tools' and matching:
                e['license']='BSD (source/upstream; package metadata reports MIT)'
                e['license_conflict']='Preserve upstream BSD text and code copyright; see supplements/PROVENANCE.json.'
        sdk_license=supplements/'Microsoft.WebView2-1.0.2957.106-LICENSE.txt'
        if sdk_license.exists():
            inventory.append(dict(name='Microsoft.Web.WebView2 SDK',version='1.0.2957.106',license='Microsoft BSD terms; see LICENSE and NOTICE',
                source='https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.2957.106/microsoft.web.webview2.1.0.2957.106.nupkg',
                license_files=['supplements/'+sdk_license.name,'supplements/Microsoft.WebView2-1.0.2957.106-NOTICE.txt']))
    inventory.append(dict(name='CPython',version=sys.version.split()[0],license='PSF/Python license and included notices',source='https://www.python.org/',license_files=['LICENSES.txt']))
    (out/'LICENSES.txt').write_text('\n'.join(texts),encoding='utf-8')
    (out/'inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=['# Third-party notices','',
          'Application source: MIT, copyright2026 Chang You. Third-party components retain their own licenses.',
          'Exact license texts and metadata are in docs/third_party/LICENSES.txt and inventory.json.',
          'No system fonts, Arroyo manuals, VISA drivers or WebView2 Runtime installers are redistributed.',
          'Microsoft .NET/WebView2/Windows components remain subject to their respective Microsoft terms.',
          '', '| Component | Locked version | License |','|---|---|---|']
    for e in inventory:rows.append('| '+e['name']+' | '+e['version']+' | '+e['license'].replace('|','/')+' |')
    rows+=['','PyInstaller includes its bootloader exception. pystray is LGPL: retain its license and source/rebuild availability; see the source URL and exact version in inventory.json.','Rebuild instructions use the same locked dependencies; no claim is made that dependency licenses are all MIT.']
    (root/'THIRD_PARTY_NOTICES.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    return inventory


if __name__=='__main__':
    collect(Path(__file__).resolve().parents[1],sys.argv[1]);print('Dependency license inventory collected.')
