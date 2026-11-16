import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from product_model import ProductModel
from hardware_control.ui_preferences import UiPreferences
from settings_commit import commit
from settings_transaction import Transaction,recover


class SettingsCommitTests(unittest.TestCase):
    def test_invalid_storage_never_saves_general_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);m=ProductModel(recording_dir=r/'log',settings_path=r/'.runtime/settings.json',simulation=True)
            ui=UiPreferences(r/'.runtime/UiPreferencesSimulation/display.json')
            try:
                initial=dict(m.settings); changed=dict(initial,display_seconds=155)
                storage=dict(directory=str(r/'other'),prefix='../bad',expected_directory=str(m.daily_log.directory),expected_prefix=m.daily_log.prefix)
                with self.assertRaises(ValueError):commit(m,ui,dict(settings=changed,storage=storage,preferences={'thermal_colors':False}))
                self.assertEqual(m.settings,initial);self.assertFalse(m.settings_path.exists());self.assertFalse(ui.path.exists())
                self.assertEqual(m.driver.parameter_write_attempts,0)
            finally:m.daily_log.close()

    def test_global_save_commits_storage_and_other_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);m=ProductModel(recording_dir=r/'log',settings_path=r/'.runtime/settings.json',simulation=True)
            ui=UiPreferences(r/'.runtime/UiPreferencesSimulation/display.json')
            try:
                storage=dict(directory=str(r/'saved'),prefix='test_log',expected_directory=str(m.daily_log.directory),expected_prefix=m.daily_log.prefix)
                result=commit(m,ui,dict(settings=dict(m.settings,display_seconds=155),storage=storage,preferences={'thermal_colors':False}))
                self.assertEqual(result['settings']['display_seconds'],155)
                self.assertEqual(m.daily_log.directory,r/'saved');self.assertEqual(ui.read(),{'thermal_colors':False})
                self.assertEqual(json.loads((m.daily_log.root/'csv_settings.json').read_text())['prefix'],'test_log')
                self.assertFalse(m.settings_path.with_name('settings-transaction.json').exists())
                self.assertEqual(m.driver.parameter_write_attempts,0)
            finally:m.daily_log.close()

    def test_two_file_failure_rolls_back_and_crash_journal_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'settings.json';b=root/'csv_settings.json';j=root/'pending.json'
            a.write_text('original-a');b.write_text('original-b')
            real=os.replace;failed=[False]
            def fail_once(src,dst):
                if Path(dst)==b and not failed[0]:failed[0]=True;raise PermissionError('fixture')
                return real(src,dst)
            with patch('settings_transaction.os.replace',side_effect=fail_once):
                with self.assertRaises(PermissionError):
                    with Transaction(j,{a:{'new':1},b:{'new':2}}):pass
            self.assertEqual(a.read_text(),'original-a');self.assertEqual(b.read_text(),'original-b')
            tx=Transaction(j,{a:{'new':1},b:{'new':2}});tx.__enter__()
            recover(j,(a,b))
            self.assertEqual(a.read_text(),'original-a');self.assertEqual(b.read_text(),'original-b')

if __name__=='__main__':unittest.main()
