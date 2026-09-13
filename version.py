"""Single source of truth for the running app's version and build time.

VERSION is bumped by hand (semver) whenever a meaningful set of changes
ships — see CHANGELOG.md. BUILD_TIME is written automatically at Docker
image build time (see Dockerfile) so two images sharing the same VERSION
during active development can still be told apart.
"""

import os

_HERE = os.path.dirname(__file__) or "."


def _read(filename: str, default: str) -> str:
    try:
        with open(os.path.join(_HERE, filename), encoding="utf-8") as f:
            return f.read().strip() or default
    except OSError:
        return default


VERSION = _read("VERSION", "0.0.0-dev")
BUILD_TIME = _read("BUILD_TIME", "unknown")
