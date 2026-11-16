"""Explicit public-source export. No runtime/profile/history/.git traversal."""
import hashlib,json,re,zipfile
from pathlib import Path

ROOT_FILES={'install.ps1','.gitignore','AGENTS.md','README.md','LICENSE','THIRD_PARTY_NOTICES.md','package.json','package-lock.json','requirements-build.lock'}
ROOT_DIRS={'src','tests','scripts','packaging','docs','.github'}
SUFFIXES={'.ps1','.py','.js','.mjs','.jsx','.ts','.json','.jsonc','.css','.html','.svg','.ico','.md','.txt','.lock','.yml','.yaml','.spec'}

def files(root):
    result=[]
    for p in Path(root).rglob('*'):
        rel=p.relative_to(root)
        if not p.is_file() or p.is_symlink() or any(x in ('__pycache__','node_modules','.git','private','.runtime') for x in rel.parts):continue
        if len(rel.parts)==1 and p.name in ROOT_FILES:result.append(p)
        elif rel.parts[0] in ROOT_DIRS and p.suffix in SUFFIXES:result.append(p)
    return sorted(result)

def export(root,output,status='PREVIEW_NOT_READY'):
    root=Path(root);output=Path(output);assert not output.exists()
    entries=[];findings=[]
    required={'install.ps1','scripts/build_release.py','scripts/build-ui.mjs','packaging/QchipTEC.spec',
              'packaging/TEC_UI_PIN.json','src/qchip_entry.py','src/packaging/qchip.ico',
              'src/frontend/main.jsx','src/frontend/index.html','src/frontend/CloudGateWindow.jsx'}
    selected=files(root)
    included={p.relative_to(root).as_posix() for p in selected}
    if not required<=included:raise ValueError('Missing build inputs: '+str(sorted(required-included)))
    pin=json.loads((root/'packaging/TEC_UI_PIN.json').read_text())
    vendor={'src/vendor/tec-ui/'+f['path'] for f in pin['files']}
    if not vendor<=included:raise ValueError('Missing pinned UI inputs: '+str(sorted(vendor-included)))
    patterns={'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
              'github_token':r'\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',
              'openai_key':r'\bsk-(?:proj-)?[A-Za-z0-9_-]{35,}',
              'aws_access_key':r'\bAKIA[0-9A-Z]{16}\b'}
    for p in selected:
        raw=p.read_bytes();rel=p.relative_to(root).as_posix()
        if p.suffix!='.ico':
            text=raw.decode('utf-8',errors='replace')
            for category,pattern in patterns.items():
                count=len(re.findall(pattern,text))
                if count:findings.append(dict(path=rel,category=category,count=count))
        entries.append(dict(path=rel,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
    if findings:raise ValueError(json.dumps(findings))
    manifest=dict(status=status,files=entries,scan=dict(categories=list(patterns),findings=findings))
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for e in entries:z.writestr(e['path'],(root/e['path']).read_bytes())
        z.writestr('SOURCE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest
