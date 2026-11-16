"""Explicit, non-destructive legacy import. Never opens a device or imports credentials."""
import argparse,hashlib,json,secrets,shutil,sqlite3,time
from pathlib import Path
from profile_paths import layout,atomic_json

DB_NAME='tec-local-log-v1.sqlite3'
APP_ID=0x514c5233

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def plain(path):
    if path.is_symlink():raise ValueError('导入源不允许符号链接：'+str(path))
    return path

def settings_file(native):
    for p in (native/'.runtime/settings.json',native/'settings.json'):
        if p.is_file():return plain(p)
    raise ValueError('未找到旧 settings.json，请明确选择旧 native 配置目录')

def inspect(native,history,csv=None):
    native,history=Path(native).resolve(),Path(history).resolve()
    setting=settings_file(native);json.loads(setting.read_text(encoding='utf-8'))
    db=plain(history/DB_NAME)
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as source:
        if source.execute('PRAGMA application_id').fetchone()[0]!=APP_ID:raise ValueError('不是 Qchip TEC 本地历史数据库')
        count=source.execute('SELECT COUNT(*) FROM samples').fetchone()[0]
    files=[]
    if csv:
        folder=Path(csv).resolve()
        if not folder.is_dir():raise ValueError('旧 CSV 目录不存在')
        for p in sorted(folder.glob('*.csv')):
            plain(p);files.append(dict(name=p.name,bytes=p.stat().st_size,sha256=digest(p)))
    return dict(native=str(native),history=str(history),settings=str(setting),rows=count,csv=str(Path(csv).resolve()) if csv else None,files=files)

def migrate(native,history,csv,install,confirm=False,host=None):
    plan=inspect(native,history,csv)
    storage=layout(install_root=install,host=host,frozen=True)
    for destination in (storage.base,storage.csv):
        try:destination.resolve().relative_to(storage.install)
        except ValueError:raise ValueError('目标路径通过链接指向安装目录外，停止导入') from None
    # Source directories must never be an ancestor of the destination.
    for old in (Path(plan['native']),Path(plan['history']))+((Path(plan['csv']),) if csv else ()):
        if storage.base==old or old in storage.base.parents or storage.install==old or old in storage.install.parents:
            raise ValueError('新安装目录不能位于旧数据目录内')
    if storage.base.exists():raise ValueError('目标数据目录已存在，不覆盖；请使用尚未启动过的新安装目录')
    if plan['files'] and storage.csv.exists():raise ValueError('目标 log 目录已存在，不覆盖')
    result=dict(action='inspect',destination=str(storage.base),default_csv=str(storage.csv),source=plan,credentials_imported=False)
    if not confirm:return result
    stage=storage.install/'data'/('import-staging-'+secrets.token_hex(12))
    stage.mkdir(parents=True,exist_ok=False)
    bundle=stage/'machine-data';(bundle/'native/.runtime').mkdir(parents=True);(bundle/'history').mkdir()
    configured=json.loads(Path(plan['settings']).read_text(encoding='utf-8'))
    configured.update(log_enabled=False,overclock_enabled=False)
    configured.setdefault('recording_dir',str(storage.csv))
    atomic_json(bundle/'native/.runtime/settings.json',configured)
    for name in ('UiPreferences','UiPreferencesSimulation'):
        pref=Path(plan['native'])/'.runtime'/name/'display.json'
        if pref.is_file():
            plain(pref);dest=bundle/'native/.runtime'/name/'display.json';dest.parent.mkdir();shutil.copyfile(pref,dest)
    source=sqlite3.connect((Path(plan['history'])/DB_NAME).as_uri()+'?mode=ro',uri=True,timeout=2)
    target=sqlite3.connect(bundle/'history'/DB_NAME)
    try:
        deadline=time.monotonic()+15
        def progress(*_):
            if time.monotonic()>deadline:raise TimeoutError('旧数据库正在忙，未完成复制；请退出旧程序后重试')
        source.backup(target,pages=256,progress=progress,sleep=.05)
        if target.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('数据库副本校验失败')
        if target.execute("SELECT 1 FROM sqlite_master WHERE name='history_dataset'").fetchone():
            # Measurements/cursors stay identical; a new installation must not
            # impersonate a copied machine's publication identity.
            target.execute('UPDATE history_dataset SET publisher_id=? WHERE singleton=1',(secrets.token_urlsafe(24),));target.commit()
        result['copied_rows']=target.execute('SELECT COUNT(*) FROM samples').fetchone()[0]
    finally:target.close();source.close()
    for name in ('interval.json','csv_settings.json'):
        old=Path(plan['history'])/name
        if old.is_file():
            plain(old);json.loads(old.read_text(encoding='utf-8'));shutil.copyfile(old,bundle/'history'/name)
    if plan['files']:
        logs=stage/'logs';logs.mkdir()
        for file in plan['files']:
            old=Path(plan['csv'])/file['name'];dest=logs/file['name'];shutil.copyfile(old,dest)
            if digest(dest)!=file['sha256'] or digest(old)!=file['sha256']:raise ValueError('旧 CSV 正在变化，未提交导入；请先退出旧程序')
    atomic_json(bundle/'portable-host.json',dict(schema=1,machine_key=storage.key,default_csv=str(storage.csv)))
    result.update(action='copied',logging_enabled=False,pairing='请在新安装中重新配对；旧凭据保留在原处')
    atomic_json(bundle/'import-receipt.json',result)
    storage.base.parent.mkdir(parents=True,exist_ok=True)
    committed=False
    try:
        bundle.rename(storage.base);committed=True
        if plan['files']: (stage/'logs').rename(storage.csv)
    except Exception:
        if committed:storage.base.rename(bundle)
        raise
    return result

def main(argv=None):
    p=argparse.ArgumentParser(description='Qchip TEC 旧数据检查/复制；不导入配对凭据，不移动原数据')
    p.add_argument('--native-source',type=Path,required=True);p.add_argument('--history-source',type=Path,required=True)
    p.add_argument('--csv-source',type=Path);p.add_argument('--install-dir',type=Path,required=True)
    p.add_argument('--confirm',action='store_true');p.add_argument('--report',type=Path)
    a=p.parse_args(argv)
    if a.report and a.report.exists():raise ValueError('报告文件已存在，不覆盖；请先选择新报告文件名')
    if a.report:
        report=a.report.resolve()
        for old in (a.native_source,a.history_source,a.csv_source):
            if old is not None and (report==old.resolve() or old.resolve() in report.parents):
                raise ValueError('报告必须放在旧数据目录之外，旧目录只读')
    result=migrate(a.native_source,a.history_source,a.csv_source,a.install_dir,a.confirm)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if a.report:
        with a.report.open('x',encoding='utf-8') as file:file.write(text)
    print(text)
    return 0

if __name__=='__main__':raise SystemExit(main())
