"""Portable startup must leave an old LOCALAPPDATA profile untouched."""
import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from profile_paths import layout

class ProfilePathsTests(unittest.TestCase):
    def test_old_selected_profile_is_retained_but_not_automatically_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();chosen=root/'existing-profile';chosen.mkdir()
            history=chosen/'existing-user-data';history.write_bytes(b'unchanged')
            launcher=root/'QchipTEC/BridgeSettingsLauncher/launcher.json';launcher.parent.mkdir(parents=True)
            launcher.write_text(json.dumps({'data_dir':str(chosen)}))
            with patch.dict(os.environ,{'LOCALAPPDATA':str(root)}):
                selected=layout(install_root=root/'installed',host='a'*64,frozen=True)
            self.assertTrue(selected.native.is_relative_to(root/'installed/data'))
            self.assertEqual(selected.csv,root/'installed/log')
            self.assertNotEqual(selected.native,chosen)
            self.assertEqual(history.read_bytes(),b'unchanged')
            self.assertEqual(json.loads(launcher.read_text())['data_dir'],str(chosen))
    def test_source_requires_explicit_profile_without_reading_old_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            launcher=root/'QchipTEC/BridgeSettingsLauncher/launcher.json';launcher.parent.mkdir(parents=True);launcher.write_text('{}')
            with patch.dict(os.environ,{'LOCALAPPDATA':str(root)}):
                with self.assertRaises(ValueError):layout(install_root=root/'installed',host='a'*64,frozen=False)
                selected=layout(root/'explicit',install_root=root/'installed',host='a'*64,frozen=False)
            self.assertEqual(selected.base,root/'explicit')
            self.assertEqual(launcher.read_text(),'{}')
            self.assertFalse((root/'installed').exists())

if __name__=='__main__':unittest.main()
