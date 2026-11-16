"""Tie tested clean Python source to the exact compiled code in the shipped EXE."""
import argparse,hashlib,json,marshal,types
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader

def normalized(value):
    if isinstance(value,types.CodeType):
        return {'code':value.co_code.hex(),'names':value.co_names,'vars':value.co_varnames,
                'free':value.co_freevars,'cells':value.co_cellvars,'flags':value.co_flags,
                'args':(value.co_argcount,value.co_posonlyargcount,value.co_kwonlyargcount),
                'line':value.co_firstlineno,'lines':value.co_lnotab.hex(),'constants':[normalized(c) for c in value.co_consts]}
    if isinstance(value,bytes):return {'bytes':value.hex()}
    if isinstance(value,tuple):return {'tuple':[normalized(v) for v in value]}
    if isinstance(value,frozenset):return {'frozenset':sorted(normalized(v) for v in value)}
    if value is Ellipsis:return {'ellipsis':True}
    return value

def fingerprint(code):return hashlib.sha256(json.dumps(normalized(code),sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('exe',type=Path);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/'src'
    archive=CArchiveReader(str(args.exe));pyz=archive.open_embedded_archive('PYZ.pyz')
    modules=['desktop','desktop_window','version','profile_paths','release_config','updates','update_helper','settings_transaction','settings_commit','product_model','hardware_control','hardware_control_entry','hardware_control.http','hardware_control.gate','hardware_control.bridge','local_log.host']
    results=[]
    for name in modules:
        path=root/Path(*name.split('.'))
        path=path/'__init__.py' if path.is_dir() else path.with_suffix('.py')
        expected=compile(path.read_bytes(),str(path),'exec',optimize=0)
        actual=pyz.extract(name)
        if fingerprint(expected)!=fingerprint(actual):raise ValueError('Compiled code differs: '+name)
        results.append({'module':name,'code_sha256':fingerprint(actual)})
    entry=marshal.loads(archive.extract('qchip_entry'))
    assert fingerprint(entry)==fingerprint(compile((root/'qchip_entry.py').read_bytes(),'entry','exec'))
    result={'exe_sha256':hashlib.sha256(args.exe.read_bytes()).hexdigest(),'entry_matches':True,'modules':results,
            'note':'Archive includes native modules by design; actual broker role non-import is checked separately.'}
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print('Compiled source matches:',len(results),'modules + entry')

if __name__=='__main__':main()
