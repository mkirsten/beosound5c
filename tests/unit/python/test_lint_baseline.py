"""Grep-lint ratchet for bug patterns that have historically caused issues.

Each check has a per-file baseline (current count as of when the check was
added).  The test fails if a file exceeds its baseline or a new file appears.
Counts may only decrease.  When you genuinely need to add an occurrence,
update the baseline below in the same commit so the change is reviewable.

Patterns checked:

  * ``asyncio.create_task(`` outside ``lib/background_tasks.py`` — untracked
    tasks bypass cancellation and exception logging (see commits e6f9b3a,
    ddd02ea, b9c8096).
  * ``sys.path.insert`` — module-name collisions across sibling source
    services (see commits 7d3f282, 84f9bb3).  Real fix is proper packaging.
  * Blocking calls (``subprocess.run``, ``time.sleep``, ``requests.*``)
    inside ``async def`` — stalls the event loop (see commits 27cb774,
    ddd02ea, 39c1169).  Detected via AST.
  * Hardcoded ``localhost:87xx`` URLs outside a central endpoints module —
    config drift, local/remote detection bugs (see commits 121cf20, 16e17a8).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[3] / "services"


def _py_files():
    return [p for p in SERVICES.rglob("*.py") if "__pycache__" not in p.parts]


def _rel(p: Path) -> str:
    return str(p.relative_to(SERVICES))


# ── Baselines ─────────────────────────────────────────────────────────────
# Counts may only decrease.  Update *down* when you remove an offender;
# update *up* only with a code review comment explaining why.

CREATE_TASK_BASELINE: dict[str, int] = {
    "input.py": 2,
    "bluetooth.py": 5,
    "router.py": 1,
    "beo6/service.py": 1,
    "players/local.py": 1,
    "players/sonos.py": 3,
    "lib/player_base.py": 1,
    "lib/watchdog.py": 2,
    "lib/transport.py": 1,
    "lib/librespot.py": 1,
    "lib/source_base.py": 1,
    "sources/demo.py": 1,
    "sources/news.py": 1,
    "sources/cd.py": 3,
    "sources/apple_music/service.py": 5,
    "sources/radio/service.py": 1,
    "sources/spotify/service.py": 5,
    "sources/spotify/spotify_auth.py": 1,
    "sources/usb/service.py": 2,
    "sources/plex/service.py": 7,
    "sources/tidal/service.py": 5,
    "lib/file_playback/file_player.py": 1,
    "lib/file_playback/remote_player.py": 3,
    "lib/file_playback/transcode_cache.py": 1,
}

SYS_PATH_INSERT_BASELINE: dict[str, int] = {
    "masterlink.py": 1,
    "bluetooth.py": 1,
    "router.py": 1,
    "beo6/service.py": 1,
    "players/local.py": 1,
    "players/sonos.py": 1,
    "players/bluesound.py": 1,
    "lib/spotify_canvas.py": 1,
    "sources/demo.py": 1,
    "sources/news.py": 2,
    "sources/cd.py": 1,
    "sources/apple_music/fetch.py": 1,
    "sources/apple_music/service.py": 2,
    "sources/apple_music/apple_music_auth.py": 1,
    "sources/radio/service.py": 1,
    "sources/spotify/fetch.py": 2,
    "sources/spotify/service.py": 2,
    "sources/spotify/spotify_auth.py": 1,
    "sources/usb/service.py": 2,
    "sources/plex/fetch.py": 1,
    "sources/plex/service.py": 2,
    "sources/plex/plex_auth.py": 2,
    "sources/tidal/fetch.py": 1,
    "sources/tidal/service.py": 2,
    "sources/tidal/tidal_auth.py": 1,
}

# subprocess.run / time.sleep / requests.* inside `async def`.  cd.py:147 is
# wrapped in run_in_executor so the AST walker skips it (see helper below);
# players/local.py:203 is a direct pkill on startup that *should* be fixed
# but isn't blocking anything in practice.
BLOCKING_IN_ASYNC_BASELINE: dict[str, int] = {
    "input.py": 2,       # systemctl list-units + hostname in _run_update / info handler
    "players/local.py": 1,
}

# Hardcoded localhost:87xx URLs outside a central endpoints module.
# All sweeped into lib/endpoints.py.  Baseline is 0 — any new literal
# fails the lint and should be added to lib/endpoints.py instead.
LOCALHOST_URL_BASELINE: dict[str, int] = {}


# ── Helpers ───────────────────────────────────────────────────────────────


_BLOCKING_CALLS = {
    "subprocess.run",
    "subprocess.check_output",
    "subprocess.check_call",
    "subprocess.call",
    "time.sleep",
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.delete",
    "requests.request",
}


class _BlockingVisitor(ast.NodeVisitor):
    """Find blocking calls that run directly on the event loop.

    A call is flagged when it appears inside an ``async def`` body *without*
    being wrapped in ``run_in_executor`` / ``to_thread``.  A nested ``def``
    breaks the async context (synchronous helper), so we reset the flag.
    """

    def __init__(self):
        self.hits: list[int] = []
        self._async_depth = 0
        self._executor_depth = 0

    def visit_AsyncFunctionDef(self, node):
        self._async_depth += 1
        self.generic_visit(node)
        self._async_depth -= 1

    def visit_FunctionDef(self, node):
        # Nested sync def — blocking calls here are fine.
        saved = self._async_depth
        self._async_depth = 0
        self.generic_visit(node)
        self._async_depth = saved

    def visit_Call(self, node):
        func_src = ast.unparse(node.func)
        is_executor = func_src.endswith("run_in_executor") or func_src.endswith(
            "asyncio.to_thread"
        )
        if is_executor:
            self._executor_depth += 1
            self.generic_visit(node)
            self._executor_depth -= 1
            return
        if (
            self._async_depth > 0
            and self._executor_depth == 0
            and func_src in _BLOCKING_CALLS
        ):
            self.hits.append(node.lineno)
        self.generic_visit(node)


def _count_blocking(path: Path) -> int:
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return 0
    v = _BlockingVisitor()
    v.visit(tree)
    return len(v.hits)


def _count_line_pattern(path: Path, pattern: re.Pattern) -> int:
    return sum(1 for line in path.read_text().splitlines() if pattern.search(line))


def _check(
    name: str,
    baseline: dict[str, int],
    counter,
    skip: set[str] | None = None,
) -> None:
    skip = skip or set()
    current: dict[str, int] = {}
    for p in _py_files():
        rel = _rel(p)
        if rel in skip:
            continue
        n = counter(p)
        if n:
            current[rel] = n

    errors: list[str] = []
    for rel, n in sorted(current.items()):
        allowed = baseline.get(rel, 0)
        if n > allowed:
            errors.append(
                f"  {rel}: {n} (baseline allows {allowed}) — "
                f"{'NEW FILE' if allowed == 0 else 'regressed'}"
            )
    for rel, allowed in sorted(baseline.items()):
        if rel not in current and allowed > 0:
            errors.append(
                f"  {rel}: 0 (baseline is {allowed}) — GOOD, please lower "
                f"the baseline to 0 in test_lint_baseline.py"
            )
        elif rel in current and current[rel] < baseline[rel]:
            errors.append(
                f"  {rel}: {current[rel]} (baseline is {allowed}) — "
                f"GOOD, please lower the baseline to {current[rel]} "
                f"in test_lint_baseline.py"
            )

    assert not errors, (
        f"\n{name} lint baseline mismatch:\n" + "\n".join(errors) + "\n\n"
        f"If you intentionally added a new offender, update "
        f"{name}_BASELINE in tests/unit/python/test_lint_baseline.py — "
        f"but first consider whether the new code should use the "
        f"right pattern instead (see the module docstring)."
    )


# ── Tests ─────────────────────────────────────────────────────────────────


def test_no_new_untracked_create_task():
    pattern = re.compile(r"\basyncio\.create_task\(")
    _check(
        "CREATE_TASK",
        CREATE_TASK_BASELINE,
        lambda p: _count_line_pattern(p, pattern),
        skip={"lib/background_tasks.py"},
    )


def test_no_new_sys_path_insert():
    pattern = re.compile(r"(?:^|\W)_?sys\.path\.insert\b")
    _check(
        "SYS_PATH_INSERT",
        SYS_PATH_INSERT_BASELINE,
        lambda p: _count_line_pattern(p, pattern),
    )


def test_no_new_blocking_calls_in_async():
    _check("BLOCKING_IN_ASYNC", BLOCKING_IN_ASYNC_BASELINE, _count_blocking)


def test_no_new_hardcoded_localhost_urls():
    pattern = re.compile(r"localhost:87\d\d")
    _check(
        "LOCALHOST_URL",
        LOCALHOST_URL_BASELINE,
        lambda p: _count_line_pattern(p, pattern),
        skip={"lib/endpoints.py"},
    )
