"""Windows release entry: exact version/tag, fresh UI, one EXE, verified assets."""
import argparse,ast,hashlib,json,os,shutil,subprocess,sys,uuid,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def product_version():
    tree=ast.parse((ROOT/'src/version.py').read_text())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PRODUCT_VERSION' for t in n.targets))

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def package(exe,out,value):
    portable=out/'portable';portable.mkdir()
    shutil.copy2(exe,portable/'Qchip TEC.exe')
    shutil.copyfile(ROOT/'LICENSE',portable/'LICENSE.txt')
    full=(ROOT/'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')+'\n'+(ROOT/'docs/third_party/LICENSES.txt').read_text(encoding='utf-8')
    (portable/'THIRD_PARTY_NOTICES.txt').write_text(full,encoding='utf-8')
    (portable/'version.json').write_text(json.dumps({'product':'QchipTEC','version':value}),encoding='utf-8')
    files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(portable.iterdir())]
    z=out/'Qchip-TEC-Windows-x64.zip'
    with zipfile.ZipFile(z,'x',compression=zipfile.ZIP_DEFLATED) as f:
        for p in sorted(portable.iterdir()):f.write(p,p.name)
    manifest=dict(schema=1,product='QchipTEC',version=value,platform='windows-x64',archive=dict(name=z.name,bytes=z.stat().st_size,sha256=sha(z)),files=files)
    (out/'update.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (out/'SHA256SUMS.txt').write_text('\n'.join(sha(out/n)+'  '+n for n in (z.name,'update.json'))+'\n',encoding='utf-8')
    sys.path.insert(0,str(ROOT/'src'))
    from updates import validate_manifest,extract_verified
    validate_manifest(manifest,value);extract_verified(z,out/'verified-package',manifest)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--version',required=True);parser.add_argument('--tag',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=product_version()
    if args.version!=value or args.tag!='v'+value:raise ValueError('Tag/version/source mismatch')
    out=Path(os.path.abspath(str(args.output)));out.mkdir(parents=True,exist_ok=True)
    if any((out/n).exists() for n in ('portable','Qchip-TEC-Windows-x64.zip','update.json')):raise ValueError('Use a clean output directory; do not overwrite a release')
    work=out/'build'/uuid.uuid4().hex;work.mkdir(parents=True)
    src=work/'src'
    shutil.copytree(ROOT/'src',src,ignore=shutil.ignore_patterns('__pycache__','*.pyc','dist','hardware_gate_dist'))
    node=os.environ.get('QCHIP_NODE','node')
    subprocess.run([node,str(ROOT/'scripts/build-ui.mjs'),'--version',value,'--pin',str(ROOT/'packaging/TEC_UI_PIN.json')],cwd=str(src),check=True)
    env=os.environ.copy();env['QCHIP_BUILD_SOURCE']=str(src)
    with (out/'build.log').open('w',encoding='utf-8') as log:
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(work/'frozen'),'--workpath',str(work/'pyinstaller'),str(ROOT/'packaging/QchipTEC.spec')],cwd=str(ROOT),env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    exe=work/'frozen/Qchip TEC.exe'
    result=work/'version-smoke.json';subprocess.run([str(exe),'--version-file',str(result)],timeout=30,check=True)
    if json.loads(result.read_text())!={'version':value}:raise ValueError('Built native version mismatch')
    result=work/'broker-version-smoke.json';subprocess.run([str(exe),'--broker','version','--result-file',str(result)],timeout=30,check=True)
    broker=json.loads(result.read_text());
    if broker['version']!=value or broker['device_access'] or broker['instrument_modules_loaded']:raise ValueError('Broker version/isolation check failed')
    package(exe,out,value)
    # Working clean-source dist must match the actual candidate executable.
    for name in ('dist','hardware_gate_dist'):
        dest=ROOT/'src'/name;dest.mkdir(exist_ok=True)
        for p in (src/name).iterdir():shutil.copyfile(p,dest/p.name)
    print('Release candidate built and package verified; not published or installed.')

if __name__=='__main__':main()
