"""CLI entry for the self-improve dev-loop (Phase 4 §4.1.A.5).

    uv run python -m plot_agent.selfimprove run

Runs the sample golden scenarios, prints the before/after Verdict as JSON, and writes
the scenario screenshots to the artifact store (default: filesystem ``.artifacts/``).
Because no external edit happens between the snapshots in a single CLI invocation, the
default verdict is all-``unchanged`` — the regression/improvement signal is exercised by
the DevLoop tests (and by Claude Code editing code between snapshots in a real session).
"""

from __future__ import annotations

import json
import sys

from plot_agent.selfimprove.loop import DevLoop
from plot_agent.selfimprove.scenarios import sample_scenarios


def _run() -> int:
    loop = DevLoop(scenarios=sample_scenarios())
    loop.snapshot_before()
    loop.reload()  # same reload path dev_reload uses (no external edit in one CLI run)
    loop.snapshot_after()
    verdict = loop.verdict(persist_screenshots=True)
    print(json.dumps(verdict.to_dict(), indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else "run"
    if cmd != "run":
        print(f"unknown command {cmd!r}; usage: python -m plot_agent.selfimprove run", file=sys.stderr)
        return 2
    return _run()


if __name__ == "__main__":
    raise SystemExit(main())
