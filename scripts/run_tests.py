"""Offline suite isolated from this Windows user's live private profile."""
import os,sys,tempfile,unittest
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[1];os.chdir(root)
    with tempfile.TemporaryDirectory(prefix='qchip-test-profile-') as folder:
        os.environ['LOCALAPPDATA']=folder
        sys.path[:0]=[str(root/'src'),str(root/'tests')]
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(root/'tests')))
        return 0 if result.wasSuccessful() else 1

if __name__=='__main__':raise SystemExit(main())
