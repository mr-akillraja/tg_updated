"""One orchestrator per OS user, across project copies and asset aliases."""
from contextlib import AbstractContextManager
import fcntl
import os
from pathlib import Path
import tempfile


class ProcessLock(AbstractContextManager):
    def __init__(self, path=None):
        # Global across copies: the browser session must have only one owner.
        self.path = Path(path or (Path(tempfile.gettempdir()) /
                                 f"tg-algo-orchestrator-{os.getuid()}.lock"))
        self._file = None

    def __enter__(self):
        self._file = self.path.open("a+")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._file.close()
            self._file = None
            raise RuntimeError("Another unified orchestrator owns the browser session")
        self._file.seek(0)
        self._file.truncate()
        self._file.write(str(os.getpid()))
        self._file.flush()
        return self

    def __exit__(self, *_):
        if self._file:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None
