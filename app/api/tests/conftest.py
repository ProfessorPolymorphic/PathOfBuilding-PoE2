"""Keep test builds and snapshots out of the real state directories."""

import os
import tempfile

_root = tempfile.mkdtemp(prefix="pob-test-state-")
os.environ.setdefault("POB_STATE_DIR", os.path.join(_root, "builds"))
os.environ.setdefault("POB_SNAPSHOT_DIR", os.path.join(_root, "snapshots"))
os.environ.setdefault("POB_CHAT_DIR", os.path.join(_root, "chats"))
