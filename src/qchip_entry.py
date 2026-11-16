"""One packaged executable, separate native/broker/update-helper processes."""
import json
import sys


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--version-file':
        from pathlib import Path
        from version import PRODUCT_VERSION
        Path(sys.argv[2]).write_text(json.dumps({'version':PRODUCT_VERSION}),encoding='utf-8')
        return 0
    if '--version' in sys.argv[1:]:
        from version import PRODUCT_VERSION
        print(json.dumps({'version': PRODUCT_VERSION}))
        return 0
    if len(sys.argv)>1 and sys.argv[1]=='--broker':
        sys.argv.pop(1)
        from hardware_control_entry import main as broker
        return broker()
    if len(sys.argv)>1 and sys.argv[1]=='--apply-update':
        from update_helper import main as update
        return update(sys.argv[2:])
    from desktop import main as desktop
    return desktop()


if __name__=='__main__':
    raise SystemExit(main())
