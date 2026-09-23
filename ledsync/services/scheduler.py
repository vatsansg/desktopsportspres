"""Windows Task Scheduler registration for scheduled runs (BRD Section 22, Section 27; Desktop BRD
Addendum A Section 39.6 - "whether scheduled runs must work with no user logged in" - confirmed by
the owner 23 September 2026: yes, it must).

BRD Section 27 assigns configuring Task Scheduler to the INSTALLER, which is Phase 13 and does not
exist yet. Until then (owner decision, 23 Sep 2026), Settings -> Scheduling registers/updates/removes
the real Task Scheduler entry itself, via `schtasks.exe`, the moment the schedule is saved - Phase 13
can take this over later without changing the schedule the operator already configured.

The Windows account PASSWORD is used only to pass to `schtasks /RP` at registration time (needed for
"run whether user is logged on or not" / LogonType=Password) and is never stored by this application
anywhere - not in the database, not in a log, not in this module. Windows' own credential store (LSA
secrets) is what actually retains it, exactly as if an administrator had typed it into the Task
Scheduler UI by hand. `schtasks`' own stdout/stderr is never echoed into any log or oplog message,
since Windows error text for a bad password can be verbose and is not worth the risk of it containing
anything sensitive.
"""

import logging
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

log = logging.getLogger("ledsync.scheduler")

TASK_NAME = "LEDAssetSync Scheduled Run"
_RUN_TIMEOUT = 30  # seconds - schtasks talking to the local Task Scheduler service, never the network

_DAY_ELEMENT = {
    "Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
    "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday",
}


class SchedulerError(Exception):
    """A registration/removal that failed; the message is fixed and safe to show."""


# A module-local seam, not `subprocess.run` directly: `subprocess` is the same singleton module
# object everywhere it is imported, so monkeypatching `subprocess.run` itself (as a naive autouse test
# fixture might) would silently break every OTHER module's unrelated subprocess calls too. Tests patch
# this name instead.
_DEFAULT_RUNNER = subprocess.run


def _invoke(args: list[str], runner=None):
    """`runner(args) -> CompletedProcess`-shaped callable; real `subprocess.run` by default, replaced
    by a fake in tests so no test ever touches a real Windows Task Scheduler."""
    run = runner or _DEFAULT_RUNNER
    try:
        return run(args, capture_output=True, text=True, timeout=_RUN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        raise SchedulerError("Could not run Windows Task Scheduler (schtasks.exe).") from None


def _task_xml(username: str, days: tuple, time_value: str, working_dir: Path) -> str:
    hour, minute = time_value.split(":")
    anchor = datetime.now().strftime("%Y-%m-%d")
    days_xml = "".join(f"<{_DAY_ELEMENT[d]} />" for d in days if d in _DAY_ELEMENT)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        "    <Description>Runs the LED Asset Download &amp; Sync application's scheduled "
        "Download &amp; Sync for every registered event (BRD Section 22). Managed entirely by the "
        "application's own Settings -&gt; Scheduling page - do not edit by hand.</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <CalendarTrigger>\n"
        f"      <StartBoundary>{anchor}T{hour}:{minute}:00</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "      <ScheduleByWeek>\n"
        f"        <DaysOfWeek>{days_xml}</DaysOfWeek>\n"
        "        <WeeksInterval>1</WeeksInterval>\n"
        "      </ScheduleByWeek>\n"
        "    </CalendarTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <UserId>{escape(username)}</UserId>\n"
        "      <LogonType>Password</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{escape(sys.executable)}</Command>\n"
        "      <Arguments>-m ledsync.scheduled_run</Arguments>\n"
        f"      <WorkingDirectory>{escape(str(working_dir))}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def register(username: str, password: str, days: tuple, time_value: str, working_dir: Path, *, runner=None) -> None:
    """Create or update the Task Scheduler entry. Raises SchedulerError (a plain, safe message) on
    failure; the caller decides what that means for the saved settings."""
    xml = _task_xml(username, days, time_value, working_dir)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as fh:
        fh.write(xml)
        xml_path = Path(fh.name)
    try:
        result = _invoke(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path),
             "/RU", username, "/RP", password, "/F"], runner)
        if result.returncode != 0:
            raise SchedulerError(
                "Windows Task Scheduler refused to register the scheduled task. Check the account "
                "name and password.")
    finally:
        xml_path.unlink(missing_ok=True)


def unregister(*, runner=None) -> None:
    """Remove the Task Scheduler entry. Safe to call when it does not exist (schtasks' own
    'not found' failure is not treated as an error here)."""
    _invoke(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], runner)


def is_registered(*, runner=None) -> bool:
    result = _invoke(["schtasks", "/Query", "/TN", TASK_NAME], runner)
    return result.returncode == 0
