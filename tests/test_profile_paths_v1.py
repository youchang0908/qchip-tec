import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from profile_paths import desktop_profile

class ProfilePathsTests(unittest.TestCase):
    def test_existing_selected_directory_survives_install_folder_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);chosen=root/'existing-profile';chosen.mkdir()
            history=chosen/'existing-user-data';history.write_bytes(b'unchanged')
            launcher=root/'QchipTEC/BridgeSettingsLauncher/launcher.json';launcher.parent.mkdir(parents=True)
            launcher.write_text(json.dumps({'data_dir':str(chosen)}))
            self.assertEqual(desktop_profile(local=root),chosen)
            self.assertEqual(history.read_bytes(),b'unchanged')
            self.assertEqual(desktop_profile(root/'explicit',local=root),root/'explicit')
    def test_default_stable_and_invalid_legacy_never_silently_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.assertEqual(desktop_profile(local=root),root/'QchipTEC/Desktop')
            p=root/'QchipTEC/BridgeSettingsLauncher/launcher.json';p.parent.mkdir(parents=True);p.write_text('{}')
            with self.assertRaises(ValueError):desktop_profile(local=root)

if __name__=='__main__':unittest.main()
