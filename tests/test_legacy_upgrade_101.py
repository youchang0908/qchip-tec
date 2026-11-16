"""1.0.0 explicit restart preservation; fake data only, no device/cloud IO."""
import hashlib,json,os,shutil,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import profile_paths
from product_model import ProductModel
from bridge_settings.pairing import Credential,CredentialStore
from hardware_control.bridge import load_config
from local_log.log_store import LogStore


class LegacyUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='qchip-legacy-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.local=self.root/'local';self.local.mkdir()
        self.previous=profile_paths._active
        self.addCleanup(setattr,profile_paths,'_active',self.previous)
        env=patch.dict(os.environ,{'LOCALAPPDATA':str(self.local)})
        env.start();self.addCleanup(env.stop)
        self.native=self.root/'old-profile'
        self.settings=self.native/'.runtime/settings.json'
        self.history=self.local/'QchipTEC/LocalLogs'/('hardware-'+hashlib.sha256(str(self.settings.resolve()).encode()).hexdigest()[:16])
        model=ProductModel(recording_dir=self.root/'user-csv',settings_path=self.settings,
                           local_log_root=self.history,simulation=True)
        model.configure(dict(model.settings,device_name='FAKE preserved name',log_enabled=False))
        model.daily_log.close()
        launcher=self.local/'QchipTEC/BridgeSettingsLauncher/launcher.json'
        launcher.parent.mkdir(parents=True)
        launcher.write_text(json.dumps({'data_dir':str(self.native)}))

    def test_old_restart_keeps_settings_history_pairing_and_private_paths(self):
        bridge=self.local/'QchipTEC/HardwareReadonly'
        fake_token='FAKE_ONLY_'+'a'*40
        CredentialStore(bridge).commit(Credential(fake_token,device_id='tec-'+'a'*24))
        config_path=bridge/'cloud_config.json';config=json.loads(config_path.read_text())
        config['token_file']=str(bridge/config['token_file'])
        config_path.write_text(json.dumps(config))  # Exact pre-1.0.1 absolute format.
        db=self.history/'tec-local-log-v1.sqlite3'
        store=LogStore(db);store.close()
        with sqlite3.connect(db) as conn:publisher=conn.execute('SELECT publisher_id FROM history_dataset').fetchone()[0]
        originals={p:p.read_bytes() for p in (self.settings,config_path,Path(config['token_file']))}
        storage=profile_paths.configure(self.native,install_root=self.root/'app',host='a'*64,frozen=True)
        self.assertEqual(storage.native,self.native)
        self.assertEqual(storage.base,self.native)  # The updater restarts with base again.
        self.assertEqual(storage.history,self.history)
        self.assertEqual(storage.bridge,bridge)
        self.assertEqual(profile_paths.native_broker_token(),self.local/'QchipTEC/HardwareControl/private/local_broker_token.txt')
        self.assertEqual(profile_paths.ui_preferences_path(storage.native),self.local/'QchipTEC/UiPreferences/display.json')
        self.assertEqual(load_config(storage.bridge)[1],fake_token)
        model=ProductModel(recording_dir=storage.csv,settings_path=self.settings,
                           local_log_root=storage.history,simulation=True)
        try:
            self.assertEqual(model.settings['device_name'],'FAKE preserved name')
            self.assertEqual(model.daily_log.root,self.history)
            self.assertEqual(model.daily_log.directory,self.root/'user-csv')
            self.assertEqual(model.driver.parameter_write_attempts,0)
            self.assertFalse(model.driver.fake.queries)
        finally:model.daily_log.close()
        self.assertFalse((self.native/'native').exists())
        for p,raw in originals.items():self.assertEqual(p.read_bytes(),raw)
        with sqlite3.connect(db) as conn:self.assertEqual(conn.execute('SELECT publisher_id FROM history_dataset').fetchone()[0],publisher)
        self.assertEqual(profile_paths.layout(storage.base,host='a'*64).native,self.native)
        with self.assertRaises(ValueError):profile_paths.layout(storage.base,host='b'*64,bind=True)

    def test_foreign_profile_cannot_select_this_users_bridge_and_defaults_stay_portable(self):
        foreign=self.root/'copied';shutil.copytree(self.native,foreign)
        with self.assertRaises(ValueError):profile_paths.layout(foreign,host='a'*64,bind=True)
        self.assertFalse((foreign/'.runtime/legacy-host.json').exists())
        fresh=profile_paths.layout(install_root=self.root/'fresh',host='a'*64,frozen=True)
        self.assertIsNone(fresh.legacy_local)
        self.assertEqual(fresh.native,self.root/'fresh/data/machines'/('a'*16)/'native')
        self.assertEqual(fresh.csv,self.root/'fresh/log')
        self.assertFalse((self.root/'fresh').exists())


if __name__=='__main__':unittest.main()
