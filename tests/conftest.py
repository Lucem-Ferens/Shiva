"""Shared test setup."""
import gc

import pytest


@pytest.fixture(scope="session", autouse=True)
def _release_qt_objects_before_exit():
    """Free every window while the application still exists.

    Windows left in reference cycles would otherwise be collected while Python shuts down, after Qt has
    already torn the application down, and the process aborts at exit although every test passed.
    """
    yield
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    app.processEvents()
    gc.collect()
    app.processEvents()
    gc.collect()
