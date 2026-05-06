#!/usr/bin/env python3
"""Beginner-proof launcher supervisor for ADAM SSM.

This wrapper starts ui_local.py, verifies that it is really healthy, and opens
either the app or an HTML diagnostic report.
"""

import argparse
import html
import json
import os
import platform
import queue
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[3]
UI_LOCAL = Path(__file__).resolve().with_name("ui_local.py")
READY_PREFIX = "SOURCE_MANAGER_READY "
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STARTUP_TIMEOUT_SECONDS = 45


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def log_dir() -> Path:
    override = os.environ.get("SOURCE_MANAGER_LOG_DIR", "").strip()
    if override:
        root = Path(override)
    elif platform.system() == "Darwin":
        root = Path.home() / "Library" / "Logs" / "ADAM-SSM"
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        root = Path(base) / "ADAM-SSM" / "Logs"
    else:
        base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        root = Path(base) / "adam-ssm"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "ADAM-SSM"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def coerce_port(value: object, default: int = DEFAULT_PORT) -> Tuple[int, str]:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return default, f"Invalid requested port {value!r}; using {default}."
    if port < 1 or port > 65535:
        return default, f"Invalid requested port {value!r}; using {default}."
    return port, ""


def parse_ready_line(line: str) -> Optional[dict]:
    if not line.startswith(READY_PREFIX):
        return None
    raw = line[len(READY_PREFIX) :].strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def classify_exit_before_ready(exit_code: Optional[int], saw_ready: bool) -> str:
    if saw_ready:
        return "stopped_after_ready" if exit_code == 0 else "stopped_after_ready_with_error"
    if exit_code == 0:
        return "exited_before_ready"
    return "failed_before_ready"


def classify_health_result(health_ok: bool) -> str:
    return "health_passed" if health_ok else "health_check_failed"


def classify_browser_result(opened: bool) -> str:
    return "browser_opened" if opened else "browser_open_failed"


def health_url(app_url: str) -> str:
    return app_url.rstrip("/") + "/api/health"


def probe_health(app_url: str, timeout_seconds: float = 2.0) -> Tuple[bool, dict, str]:
    try:
        req = Request(health_url(app_url), headers={"User-Agent": "ADAM-SSM-Launcher/1.0"})
        with urlopen(req, timeout=timeout_seconds) as resp:  # nosec B310 (local app URL)
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return False, {}, str(exc)
    if not isinstance(payload, dict):
        return False, {}, "Health endpoint returned non-object JSON."
    if payload.get("ok") is True and payload.get("status") == "ready":
        return True, payload, ""
    return False, payload, f"Health endpoint was not ready: {payload}"


def tail_lines(path: Path, count: int = 80) -> List[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-count:]


def write_diagnostics_html(path: Path, state: Dict[str, object]) -> None:
    log_path = str(state.get("log_path", ""))
    status = str(state.get("classification") or state.get("status") or "unknown")
    app_url = str(state.get("url") or "")
    details = str(state.get("message") or state.get("error") or "")
    log_tail = "\n".join(str(line) for line in state.get("log_tail", []) or [])
    status_path = str(state.get("status_file", ""))
    ready_path = str(state.get("ready_file", ""))
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ADAM SSM Launch Diagnostics</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; line-height: 1.45; color: #242424; }}
    main {{ max-width: 900px; }}
    h1 {{ font-size: 26px; margin-bottom: 8px; }}
    .status {{ display: inline-block; padding: 6px 10px; border: 1px solid #b3261e; color: #7b1f18; background: #fff4f2; border-radius: 6px; font-weight: 700; }}
    .box {{ border: 1px solid #d8d8d8; border-radius: 6px; padding: 14px; margin: 16px 0; background: #fafafa; }}
    code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; background: #1f2428; color: #f6f8fa; padding: 14px; border-radius: 6px; }}
    a {{ color: #0b57d0; }}
  </style>
</head>
<body>
<main>
  <h1>ADAM SSM did not open normally</h1>
  <p class="status">{html.escape(status)}</p>
  <div class="box">
    <p><strong>What to do:</strong> Take a screenshot of this page and send it to the project maintainer.</p>
    <p>If an app URL is shown below, you can also try opening it directly.</p>
  </div>
  <div class="box">
    <p><strong>App URL:</strong> {f'<a href="{html.escape(app_url)}">{html.escape(app_url)}</a>' if app_url else 'Not available'}</p>
    <p><strong>Details:</strong> {html.escape(details or 'No extra details were reported.')}</p>
    <p><strong>Log path:</strong> <code>{html.escape(log_path)}</code></p>
    <p><strong>Status file:</strong> <code>{html.escape(status_path)}</code></p>
    <p><strong>Ready file:</strong> <code>{html.escape(ready_path)}</code></p>
  </div>
  <h2>Recent Log Lines</h2>
  <pre>{html.escape(log_tail or 'No log lines were captured.')}</pre>
</main>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def open_url(url: str) -> bool:
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:
        return False


def open_diagnostics(path: Path) -> bool:
    try:
        return open_url(path.resolve().as_uri())
    except ValueError:
        return False


def terminate_process(proc: subprocess.Popen, timeout_seconds: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout_seconds)


def start_reader_thread(proc: subprocess.Popen, log_file, line_queue: "queue.Queue[str]") -> threading.Thread:
    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            log_file.write(line)
            log_file.flush()
            line_queue.put(line.rstrip("\n"))

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return thread


def supervise(args: argparse.Namespace) -> int:
    port, port_warning = coerce_port(args.port)
    run_stamp = now_stamp()
    logs = log_dir()
    log_path = logs / f"source-manager-{run_stamp}.log"
    diagnostics_path = logs / f"source-manager-diagnostics-{run_stamp}.html"
    ready_file = logs / f"source-manager-ready-{run_stamp}.json"
    status_file = logs / f"source-manager-status-{run_stamp}.json"
    state: Dict[str, object] = {
        "classification": "starting",
        "log_path": str(log_path),
        "diagnostics_path": str(diagnostics_path),
        "ready_file": str(ready_file),
        "status_file": str(status_file),
        "message": port_warning,
    }

    cmd = [
        sys.executable,
        str(UI_LOCAL),
        "--host",
        args.host,
        "--port",
        str(port),
        "--ready-file",
        str(ready_file),
        "--status-file",
        str(status_file),
    ]
    for source_arg, value in [
        ("--registry", args.registry),
        ("--aliases", args.aliases),
        ("--change-log", args.change_log),
    ]:
        if value:
            cmd.extend([source_arg, value])

    print("ADAM SSM - Sleepless Source Manager")
    print(f"Requested bind: {args.host}:{port}")
    if port_warning:
        print(port_warning)
    print(f"Log path: {log_path}")
    print(f"Diagnostics path: {diagnostics_path}")

    line_queue: "queue.Queue[str]" = queue.Queue()
    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write("ADAM SSM launcher supervisor\n")
        log_file.write(f"Command: {' '.join(cmd)}\n")
        if port_warning:
            log_file.write(port_warning + "\n")
        log_file.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        start_reader_thread(proc, log_file, line_queue)

        ready_payload = None
        deadline = time.time() + args.startup_timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                line = line_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            parsed = parse_ready_line(line)
            if parsed:
                ready_payload = parsed
                break

        if not ready_payload:
            exit_code = proc.poll()
            if exit_code is None:
                state["classification"] = "no_ready_url"
                state["message"] = "The app did not print a ready URL before the startup timeout."
                terminate_process(proc)
            else:
                state["classification"] = classify_exit_before_ready(exit_code, False)
                state["message"] = f"The app exited with code {exit_code} before reporting readiness."
            state["log_tail"] = tail_lines(log_path)
            write_diagnostics_html(diagnostics_path, state)
            open_diagnostics(diagnostics_path)
            print(f"Final classification: {state['classification']}")
            print(f"Diagnostics opened: {diagnostics_path}")
            return 1

        app_url = str(ready_payload.get("url") or "")
        state["url"] = app_url
        state["ready_payload"] = ready_payload
        health_ok, health_payload, health_error = probe_health(app_url)
        state["health_payload"] = health_payload
        if not health_ok:
            state["classification"] = classify_health_result(False)
            state["message"] = health_error
            state["log_tail"] = tail_lines(log_path)
            write_diagnostics_html(diagnostics_path, state)
            open_diagnostics(diagnostics_path)
            terminate_process(proc)
            print("Final classification: health_check_failed")
            print(f"Diagnostics opened: {diagnostics_path}")
            return 1

        if not args.no_open_browser:
            if not open_url(app_url):
                state["classification"] = classify_browser_result(False)
                state["message"] = "The app is healthy, but the launcher could not open the browser automatically."
                state["log_tail"] = tail_lines(log_path)
                write_diagnostics_html(diagnostics_path, state)
                open_diagnostics(diagnostics_path)
                print("Final classification: browser_open_failed")
                print(f"Diagnostics opened: {diagnostics_path}")
            else:
                print(f"Opened ADAM SSM: {app_url}")
        else:
            print(f"ADAM SSM ready: {app_url}")

        exit_code = proc.wait()

    final_classification = classify_exit_before_ready(exit_code, True)
    state["classification"] = final_classification
    state["message"] = f"The app process exited with code {exit_code}."
    state["log_tail"] = tail_lines(log_path)
    write_diagnostics_html(diagnostics_path, state)
    print(f"Final classification: {final_classification}")
    print(f"Log path: {log_path}")
    if final_classification == "stopped_after_ready":
        return 0
    print(f"Diagnostics path: {diagnostics_path}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("SOURCE_MANAGER_HOST", DEFAULT_HOST))
    parser.add_argument("--port", default=os.environ.get("SOURCE_MANAGER_PORT", str(DEFAULT_PORT)))
    parser.add_argument("--registry", default="")
    parser.add_argument("--aliases", default="")
    parser.add_argument("--change-log", default="")
    parser.add_argument("--startup-timeout", type=float, default=STARTUP_TIMEOUT_SECONDS)
    parser.add_argument("--no-open-browser", action="store_true", help="Do not open the app browser window.")
    return parser


def main() -> int:
    return supervise(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
