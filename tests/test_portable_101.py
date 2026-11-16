"""Portable layout and controlled imports, exclusively synthetic files/databases."""
import hashlib,json,shutil,sqlite3,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import profile_paths
from profile_paths import layout
from portable_import import migrate
from local_log.log_store import LogStore,utc
from bridge_settings.pairing import Credential,CredentialStore,permitted_token_path

class PortableTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.previous=profile_paths._active
    def tearDown(self):
        profile_paths._active=self.previous;self.tmp.cleanup()

    def test_defaults_explicit_and_new_pc_do_not_load_copied_identity(self):
        install=self.root/'app'
        a=layout(install_root=install,host='a'*64,frozen=True,bind=True)
        a.bridge.mkdir();(a.bridge/'FAKE-identity').write_text('fake old machine')
        b=layout(install_root=install,host='b'*64,frozen=True,bind=True)
        self.assertNotEqual(a.base,b.base);self.assertFalse((b.bridge/'FAKE-identity').exists())
        self.assertEqual(a.csv,install/'log')
        self.assertEqual(layout(self.root/'explicit',host='a'*64).base,self.root/'explicit')
        with self.assertRaises(ValueError):layout(a.base,host='b'*64,bind=True)
        with self.assertRaises(ValueError):layout(frozen=False)
        unmarked=self.root/'unmarked';(unmarked/'bridge').mkdir(parents=True)
        (unmarked/'bridge/FAKE-identity').write_text('old copied identity')
        with self.assertRaises(ValueError):layout(unmarked,host='a'*64,bind=True)
        self.assertFalse((unmarked/'portable-host.json').exists())

    def test_same_machine_move_rebases_only_default_csv_not_custom_directory(self):
        a=layout(install_root=self.root/'old',host='a'*64,frozen=True,bind=True)
        settings=a.native/'.runtime/settings.json';settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'recording_dir':str(a.csv)}))
        a.history.mkdir();(a.history/'csv_settings.json').write_text(json.dumps({'directory':str(self.root/'custom'),'prefix':'FAKE'}))
        copied=self.root/'moved';shutil.copytree(a.install,copied)
        b=layout(install_root=copied,host='a'*64,frozen=True,bind=True)
        self.assertEqual(json.loads((b.native/'.runtime/settings.json').read_text())['recording_dir'],str(copied/'log'))
        self.assertEqual(json.loads((b.history/'csv_settings.json').read_text())['directory'],str(self.root/'custom'))
        self.assertEqual(json.loads(settings.read_text())['recording_dir'],str(a.csv))

    def test_relative_pairing_paths_survive_same_pc_folder_move(self):
        a=profile_paths.configure(install_root=self.root/'old',host='a'*64,frozen=True)
        token='FAKE_ONLY_'+'x'*40
        CredentialStore(a.bridge).commit(Credential(token,device_id='tec-'+'a'*24))
        config=json.loads((a.bridge/'cloud_config.json').read_text())
        self.assertFalse(Path(config['token_file']).is_absolute())
        new=self.root/'new';shutil.copytree(a.install,new)
        b=profile_paths.configure(install_root=new,host='a'*64,frozen=True)
        from hardware_control.bridge import load_config
        self.assertEqual(load_config(b.bridge)[1],token)
        self.assertFalse(permitted_token_path(b.bridge,'../../foreign-token.txt'))

    def test_import_is_readonly_until_confirm_then_copies_without_identity_or_overwrite(self):
        native=self.root/'legacy-native';(native/'.runtime/private').mkdir(parents=True)
        settings=native/'.runtime/settings.json';settings.write_text(json.dumps({'recording_dir':str(self.root/'custom-csv'),'filename_prefix':'FAKE','log_enabled':True}))
        (native/'.runtime/private/device_token.txt').write_text('FAKE_ONLY_NEVER_IMPORT')
        history=self.root/'legacy-history';history.mkdir();db=history/'tec-local-log-v1.sqlite3'
        store=LogStore(db)
        stamp=1794800000123
        row=dict(session_id='FAKE-LEGACY',seq=1,channel=1,ts_ms=stamp,utc=utc(stamp),temperature_c=20.,resistance_kohm=12.,current_a=.1,voltage_v=.2,power_w=.02,tset_c=20.,output=1,mode='T',metadata_utc=utc(stamp),quality='ok',segment=0,source='simulation')
        store.submit(row);store.flush();store.close()
        with sqlite3.connect(db) as source:old_id=source.execute('SELECT publisher_id FROM history_dataset').fetchone()[0]
        csv=self.root/'old-csv';csv.mkdir();raw=csv/'FAKE.csv';raw.write_bytes(b'FAKE,original,CSV\r\n1,2,3\r\n')
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (settings,db,raw)}
        dest=self.root/'new-app'
        report=self.root/'existing-report.json';report.write_text('keep existing report')
        from portable_import import main
        with self.assertRaises(ValueError):main(['--native-source',str(native),'--history-source',str(history),'--install-dir',str(dest),'--confirm','--report',str(report)])
        self.assertFalse(dest.exists());self.assertEqual(report.read_text(),'keep existing report')
        inspected=migrate(native,history,csv,dest,host='a'*64)
        self.assertEqual(inspected['action'],'inspect');self.assertFalse(dest.exists())
        copied=migrate(native,history,csv,dest,True,host='a'*64)
        paths=layout(install_root=dest,host='a'*64,frozen=True)
        self.assertEqual(copied['copied_rows'],1)
        saved=json.loads((paths.native/'.runtime/settings.json').read_text())
        self.assertFalse(saved['log_enabled']);self.assertEqual(saved['recording_dir'],str(self.root/'custom-csv'))
        self.assertFalse(paths.bridge.exists());self.assertFalse((paths.native/'.runtime/private/device_token.txt').exists())
        self.assertEqual((paths.csv/'FAKE.csv').read_bytes(),raw.read_bytes())
        with sqlite3.connect(paths.history/'tec-local-log-v1.sqlite3') as target:
            self.assertEqual(target.execute('SELECT ts_ms,utc FROM samples').fetchone(),(stamp,utc(stamp)))
            self.assertNotEqual(target.execute('SELECT publisher_id FROM history_dataset').fetchone()[0],old_id)
        for p,digest in before.items():self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),digest)
        with self.assertRaises(ValueError):migrate(native,history,csv,dest,True,host='a'*64)

    def test_update_stage_inside_data_preserves_data_and_log(self):
        from test_updates_v1 import fixture,sha
        from updates import extract_verified
        from update_helper import apply
        target=self.root/'installed';target.mkdir();(target/'Qchip TEC.exe').write_bytes(b'old program')
        (target/'data').mkdir();(target/'data/FAKE-setting.json').write_bytes(b'unchanged settings')
        (target/'log').mkdir();(target/'log/FAKE.csv').write_bytes(b'unchanged log')
        cache=target/'data/updates';cache.mkdir();archive,manifest=fixture(cache)
        extract_verified(archive,cache/'program',manifest)
        plan=dict(schema=1,target=str(target),staged=str(cache/'program'),manifest=manifest,old_sha256=sha(b'old program'),pids=[],restart_args=['--device','simulation','--port','28774','--data-dir',str(self.root/'profile')])
        self.assertTrue(apply(plan,wait=lambda *_:None))
        self.assertEqual((target/'data/FAKE-setting.json').read_bytes(),b'unchanged settings')
        self.assertEqual((target/'log/FAKE.csv').read_bytes(),b'unchanged log')

if __name__=='__main__':unittest.main()
