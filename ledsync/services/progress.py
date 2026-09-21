"""Progress and cancellation of a run, shared between the worker thread and the page that polls it.

A Download & Sync run has phases (Checking, Downloading, Synchronising). Within a phase `total`/`done`/`failed` count the
files of THAT phase; `identified`, `downloaded`, `synchronised` and `errors` are running totals for the whole run and are what
the dashboard's Operation Status shows (BRD Section 24).
"""

import threading


class Progress:
    """Thread-safe counters for one run. The worker updates them; the web page reads `snapshot()`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self.state = "idle"                    # idle | running | done | cancelled | error
        self.phase = ""                        # Checking | Downloading | Synchronising
        self.total = 0                         # files to process in this phase
        self.done = 0                          # files finished in this phase (any outcome)
        self.failed = 0
        self.current = ""                      # the file being processed
        self.bytes_total = 0                   # size of the current file
        self.bytes_done = 0
        self.message = ""                      # a short plain summary once finished
        self.final_state = "done"              # what `state` becomes when the job ends: done | cancelled | error
        self.identified = 0                    # files found to download or push (whole run)
        self.downloaded = 0
        self.synchronised = 0
        self.errors = 0

    # --- worker side -------------------------------------------------------------------------------
    def start(self, total: int) -> None:
        with self._lock:
            self.state, self.total, self.done, self.failed = "running", total, 0, 0
            self.current, self.bytes_total, self.bytes_done, self.message = "", 0, 0, ""

    def set_phase(self, name: str, total: int) -> None:
        with self._lock:
            self.phase, self.total, self.done, self.failed = name, total, 0, 0
            self.current, self.bytes_total, self.bytes_done = "", 0, 0

    def identify(self, count: int) -> None:
        with self._lock:
            self.identified += count

    def tally_downloaded(self) -> None:
        with self._lock:
            self.downloaded += 1

    def tally_synchronised(self) -> None:
        with self._lock:
            self.synchronised += 1

    def tally_error(self) -> None:
        with self._lock:
            self.errors += 1

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
            return dict(state=self.state, phase=self.phase, total=self.total, done=self.done, failed=self.failed,
                        current=self.current, percent=min(percent, 100), message=self.message,
                        cancelling=self._cancel.is_set(), identified=self.identified, downloaded=self.downloaded,
                        synchronised=self.synchronised, errors=self.errors)


class NullProgress(Progress):
    """Used when nobody is watching (tests, headless runs): counts nothing, is never cancelled."""

    def start(self, total): pass
    def set_phase(self, name, total): pass
    def identify(self, count): pass
    def tally_downloaded(self): pass
    def tally_synchronised(self): pass
    def tally_error(self): pass
    def begin(self, name, size): pass
    def add_bytes(self, count): pass
    def finish_file(self, ok): pass
    def end(self, state, message): pass
