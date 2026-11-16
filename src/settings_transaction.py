"""Crash-recoverable transaction for a fixed set of local settings files only."""
import base64
import json
import os
from pathlib import Path
import secrets


def write_bytes(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
    try:
        with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(str(tmp),str(path))
    finally:
        if tmp.exists():tmp.unlink()


def recover(journal, allowed):
    journal=Path(journal)
    if not journal.exists():return
    entries=json.loads(journal.read_text(encoding='utf-8'))
    if not isinstance(entries,list) or len(entries)>3:raise ValueError('Invalid settings recovery record')
    allowed={str(Path(p).resolve()) for p in allowed}
    for e in entries:
        if set(e)!={'path','previous'} or e['path'] not in allowed:
            raise ValueError('Unexpected settings recovery path')
    for e in reversed(entries):
        p=Path(e['path'])
        if e['previous'] is None:
            if p.exists():p.unlink()
        else:write_bytes(p,base64.b64decode(e['previous'],validate=True))
    journal.unlink()


class Transaction:
    def __init__(self,journal,updates):
        self.journal=Path(journal)
        self.updates={Path(p).resolve():json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8') for p,value in updates.items()}
    def __enter__(self):
        recover(self.journal,self.updates)
        self.previous=[dict(path=str(p),previous=base64.b64encode(p.read_bytes()).decode('ascii') if p.exists() else None) for p in self.updates]
        write_bytes(self.journal,json.dumps(self.previous).encode('utf-8'))
        try:
            for p,raw in self.updates.items():write_bytes(p,raw)
        except Exception:
            recover(self.journal,self.updates)
            raise
        return self
    def __exit__(self,kind,value,tb):
        if kind:recover(self.journal,self.updates)
        else:self.journal.unlink()


def journal_paths(settings_path,log_root,ui_path):
    return (Path(settings_path),Path(log_root)/'csv_settings.json',Path(ui_path))
