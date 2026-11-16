"""Explicit disposable product storage for offline simulation fixtures."""
from pathlib import Path
import tempfile

from product_model import ProductModel


def isolated_model(test_case, **options):
    directory = tempfile.TemporaryDirectory(prefix='qchip-model-fixture-')
    test_case.addCleanup(directory.cleanup)
    root = Path(directory.name).resolve()
    model = ProductModel(recording_dir=root/'log',
                         settings_path=root/'native/.runtime/settings.json',
                         local_log_root=root/'history', **options)
    test_case.addCleanup(model.daily_log.close)
    return model
