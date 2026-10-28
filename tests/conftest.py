"""Keep application state writable and isolated before test-module imports."""
import tempfile

import pytest


def pytest_configure(config):
    # webapp persists its signing key during import, before fixtures can run.
    # Never use Docker's /output default or a developer's real OUTPUT_DIR.
    output = tempfile.TemporaryDirectory(prefix="sundaysignal-tests-")
    config.add_cleanup(output.cleanup)
    env = pytest.MonkeyPatch()
    config.add_cleanup(env.undo)
    env.setenv("OUTPUT_DIR", output.name)
