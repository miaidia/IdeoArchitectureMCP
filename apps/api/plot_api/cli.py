"""``plot-analyzer`` CLI (Phase 14B; F-0463/0466).

A thin command-line front over the HTTP API via the Python SDK
(:class:`plot_shared.PlotAnalyzerClient`) — the CLI contains NO domain logic
(the §27 no-drift rule applies to it too). Commands:

* ``plot-analyzer analyze <parcel_id> [--mode quick_screening]`` — run an
  analysis, print the decision + analysis id (or full JSON with ``--json``);
* ``plot-analyzer report <analysis_id> [--format md] [--audience architect]``
  — fetch a report; Markdown content prints raw, other formats print JSON;
* ``plot-analyzer status <analysis_id>`` / ``plot-analyzer resolve <parcel_id>``
  / ``plot-analyzer health``.

Connection: ``--api-url`` (default ``http://localhost:8000``; the combined
Docker process serves the API under ``/api`` — pass
``--api-url http://host:8000/api`` there) and ``--api-key`` /
``PLOT_ANALYZER_API_KEY`` env (never hardcoded — F-0477).

Tests call :func:`main` directly with an injected client (httpx MockTransport)
— no subprocess.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx
from plot_shared import PlotAnalyzerClient

API_KEY_ENV = "PLOT_ANALYZER_API_KEY"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plot-analyzer",
        description="Plot Analyzer CLI — thin client over the HTTP API (§27).",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="API root (combined Docker process: http://host:8000/api).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help=f"API key (default: ${API_KEY_ENV} from the environment).",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Print the full JSON response."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="Run an analysis for a parcel id.")
    analyze.add_argument("parcel_id")
    analyze.add_argument(
        "--mode",
        default="quick_screening",
        choices=["quick_screening", "full_due_diligence", "design_feasibility"],
    )

    report = sub.add_parser("report", help="Fetch a report for an analysis id.")
    report.add_argument("analysis_id")
    report.add_argument("--format", default="md")
    report.add_argument("--audience", default="architect")
    report.add_argument("--variant-id", default=None)

    status = sub.add_parser("status", help="Fetch analysis status.")
    status.add_argument("analysis_id")

    resolve = sub.add_parser("resolve", help="Resolve a parcel id via ULDK.")
    resolve.add_argument("parcel_id")

    sub.add_parser("health", help="API liveness (no key required).")
    return parser


def _print(payload: dict[str, Any], *, as_json: bool, lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for line in lines:
        print(line)


def run(args: argparse.Namespace, client: PlotAnalyzerClient) -> int:
    """Execute one parsed command against the injected client."""
    if args.command == "health":
        payload = client.healthz()
        _print(payload, as_json=args.as_json, lines=[f"status: {payload.get('status')}"])
        return 0
    if args.command == "analyze":
        payload = client.analyze({"parcel_id": args.parcel_id}, analysis_mode=args.mode)
        _print(
            payload,
            as_json=args.as_json,
            lines=[
                f"analysis_id: {payload.get('analysis_id')}",
                f"status: {payload.get('status')}",
                f"decision: {payload.get('decision')}",
                f"risks: {len(payload.get('risks', []))}; "
                f"unknowns: {len(payload.get('unknowns', []))}",
            ],
        )
        return 0
    if args.command == "report":
        payload = client.report(
            args.analysis_id,
            format=args.format,
            audience=args.audience,
            variant_id=args.variant_id,
        )
        content = payload.get("content")
        if isinstance(content, str) and not args.as_json:
            print(content)
        else:
            _print(
                payload,
                as_json=True,
                lines=[],
            )
        return 0
    if args.command == "status":
        payload = client.get_status(args.analysis_id)
        _print(
            payload,
            as_json=args.as_json,
            lines=[
                f"status: {payload.get('status')}",
                f"progress: {payload.get('progress')}",
            ],
        )
        return 0
    if args.command == "resolve":
        payload = client.resolve_parcel(parcel_id=args.parcel_id)
        _print(
            payload,
            as_json=args.as_json,
            lines=[
                f"id: {payload.get('id')}",
                f"teryt: {payload.get('teryt')}",
                f"source_id: {payload.get('source_id')}",
            ],
        )
        return 0
    raise SystemExit(2)  # pragma: no cover - argparse enforces the choices


def main(argv: list[str] | None = None, *, client: PlotAnalyzerClient | None = None) -> int:
    """CLI entry point; tests inject ``client`` (httpx MockTransport — no subprocess)."""
    args = _build_parser().parse_args(argv)
    if client is None:  # pragma: no cover - exercised via the injected-client tests
        client = PlotAnalyzerClient(
            base_url=args.api_url, api_key=args.api_key or os.environ.get(API_KEY_ENV)
        )
    try:
        return run(args, client)
    except httpx.HTTPStatusError as exc:
        print(
            f"API error {exc.response.status_code}: {exc.response.text}", file=sys.stderr
        )
        return 1
    except httpx.HTTPError as exc:
        print(f"Connection error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
