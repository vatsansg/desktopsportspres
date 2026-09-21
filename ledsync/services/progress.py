"""Progress and cancellation of a download run, shared between the worker thread and the page that polls it."""

import threading


class Progress:
    """Thread-safe counters for one run. The worker updates them; the web page reads `snapshot()`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self.state = "idle"                    # idle | running | done | cancelled | error
        self.total = 0                         # files to process
        self.done = 0                          # files finished (any outcome)
        self.failed = 0
        self.current = ""                      # the file being downloaded
        self.bytes_total = 0                   # size of the current file
        self.bytes_done = 0
        self.message = ""                      # a short plain summary once finished

    # --- worker side -------------------------------------------------------------------------------
    def start(self, total: int) -> None:
        with self._lock:
            self.state, self.total, self.done, self.failed = "running", total, 0, 0
            self.current, self.bytes_total, self.bytes_done, self.message = "", 0, 0, ""

    def begin(self, name: str, size: int | None) -> None:
        with self._lock:
            self.current, self.bytes_total, self.bytes_done = name, size or 0, 0

    def add_bytes(self, count: int) -> None:
        with self._lock:
            self.bytes_done += count

    def finish_file(self, ok: bool) -> None:
        with self._lock:
            self.done += 1
            self.failed += 0 if ok else 1
            self.current, self.bytes_total, self.bytes_done = "", 0, 0

    def end(self, state: str, message: str) -> None:
        with self._lock:
            self.state, self.message, self.current = state, message, ""

    # --- both sides ----------------------------------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def snapshot(self) -> dict:
        with self._lock:
            percent = int(100 * self.bytes_done / self.bytes_total) if self.bytes_total else 0
            return dict(state=self.state, total=self.total, done=self.done, failed=self.failed, current=self.current,
                        percent=min(percent, 100), message=self.message, cancelling=self._cancel.is_set())


class NullProgress(Progress):
    """Used when nobody is watching (tests, headless runs): counts nothing, is never cancelled."""

    def start(self, total): pass
    def begin(self, name, size): pass
    def add_bytes(self, count): pass
    def finish_file(self, ok): pass
    def end(self, state, message): pass
