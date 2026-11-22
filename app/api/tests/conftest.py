"""Keep test builds out of the real state directory."""

import os
import tempfile

os.environ.setdefault("POB_STATE_DIR", tempfile.mkdtemp(prefix="pob-test-state-"))
