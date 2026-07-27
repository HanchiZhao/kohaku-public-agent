from __future__ import annotations

import argparse
import os
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


EXIT_SUCCESS = 0
EXIT_MONITOR_FAILURE = 1
EXIT_SUCCESS_MARKER_MISSING = 3
EXIT_TIMEOUT = 124

_OUTPUT_END = object()


@dataclass(frozen=True, slots=True)
class ProcessRunResult:
    """Outcome of one monitored child process."""

    child_return_code: int | None
    exit_code: int
    success_marker_seen: bool
    timed_out: bool
    timeout_reason: str | None
    elapsed_seconds: float


def _display_command(
    command: tuple[str, ...],
) -> str:
    """Return a readable command line."""

    if os.name == "nt":
        return subprocess.list2cmdline(
            command
        )

    return shlex.join(command)


def _terminate_process_tree(
    process: subprocess.Popen[str],
) -> None:
    """Terminate one child and all descendants."""

    if process.poll() is not None:
        return

    if os.name == "nt":
        subprocess.run(
            [
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    else:
        try:
            os.killpg(
                process.pid,
                signal.SIGTERM,
            )

        except ProcessLookupError:
            return

        try:
            process.wait(
                timeout=3.0
            )

        except subprocess.TimeoutExpired:
            try:
                os.killpg(
                    process.pid,
                    signal.SIGKILL,
                )

            except ProcessLookupError:
                pass

    try:
        process.wait(
            timeout=5.0
        )

    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(
            timeout=5.0
        )


def _start_output_reader(
    stream: TextIO,
    output_queue: queue.Queue[
        str | object
    ],
) -> threading.Thread:
    """Read child output without blocking timeout checks."""

    def read_output() -> None:
        try:
            for line in iter(
                stream.readline,
                "",
            ):
                output_queue.put(
                    line
                )

        finally:
            output_queue.put(
                _OUTPUT_END
            )

    thread = threading.Thread(
        target=read_output,
        name="process-output-reader",
        daemon=True,
    )

    thread.start()

    return thread


def run_monitored_process(
    command: list[str] | tuple[str, ...],
    *,
    success_marker: str,
    overall_timeout_seconds: float,
    exit_grace_seconds: float,
    label: str,
    cwd: Path | None = None,
    output_stream: TextIO | None = None,
) -> ProcessRunResult:
    """Run a child and require real exit after success.

    A slow live model test may use the full overall
    timeout. After the child prints its success marker,
    however, it receives only the shorter exit grace
    period.

    This catches interpreter shutdown hangs that occur
    after the smoke-test body has already completed.
    """

    if not command:
        raise ValueError(
            "command must not be empty"
        )

    if not success_marker:
        raise ValueError(
            "success_marker must not be empty"
        )

    if overall_timeout_seconds <= 0:
        raise ValueError(
            "overall_timeout_seconds "
            "must be positive"
        )

    if exit_grace_seconds <= 0:
        raise ValueError(
            "exit_grace_seconds "
            "must be positive"
        )

    command_tuple = tuple(
        str(part)
        for part in command
    )

    stream = (
        output_stream
        if output_stream is not None
        else sys.stdout
    )

    print(
        "=" * 72,
        file=stream,
        flush=True,
    )

    print(
        f"Starting monitored process: "
        f"{label}",
        file=stream,
        flush=True,
    )

    print(
        "=" * 72,
        file=stream,
        flush=True,
    )

    print(
        "Command: "
        + _display_command(
            command_tuple
        ),
        file=stream,
        flush=True,
    )

    print(
        "Overall timeout: "
        f"{overall_timeout_seconds:.1f} "
        "seconds",
        file=stream,
        flush=True,
    )

    print(
        "Exit grace after success marker: "
        f"{exit_grace_seconds:.1f} "
        "seconds",
        file=stream,
        flush=True,
    )

    print(
        f"Success marker: "
        f"{success_marker}",
        file=stream,
        flush=True,
    )

    print(
        "-" * 72,
        file=stream,
        flush=True,
    )

    child_environment = (
        os.environ.copy()
    )

    child_environment[
        "PYTHONUTF8"
    ] = "1"

    child_environment[
        "PYTHONIOENCODING"
    ] = "utf-8"

    popen_kwargs: dict[
        str,
        object,
    ] = {
        "cwd": (
            str(cwd)
            if cwd is not None
            else None
        ),
        "env": child_environment,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }

    if os.name == "nt":
        popen_kwargs[
            "creationflags"
        ] = (
            subprocess
            .CREATE_NEW_PROCESS_GROUP
        )

    else:
        popen_kwargs[
            "start_new_session"
        ] = True

    started_at = (
        time.monotonic()
    )

    process = subprocess.Popen(
        command_tuple,
        **popen_kwargs,
    )

    assert process.stdout is not None

    output_queue: queue.Queue[
        str | object
    ] = queue.Queue()

    reader_thread = (
        _start_output_reader(
            process.stdout,
            output_queue,
        )
    )

    marker_seen = False

    marker_seen_at: (
        float | None
    ) = None

    reader_finished = False

    timeout_reason: (
        str | None
    ) = None

    try:
        while True:
            try:
                item = output_queue.get(
                    timeout=0.1
                )

            except queue.Empty:
                item = None

            if item is _OUTPUT_END:
                reader_finished = True

            elif isinstance(
                item,
                str,
            ):
                print(
                    item,
                    end="",
                    file=stream,
                    flush=True,
                )

                if (
                    not marker_seen
                    and success_marker in item
                ):
                    marker_seen = True

                    marker_seen_at = (
                        time.monotonic()
                    )

                    print(
                        "[monitor] Success marker "
                        "observed. Waiting for the "
                        "child process to exit.",
                        file=stream,
                        flush=True,
                    )

            now = time.monotonic()

            child_return_code = (
                process.poll()
            )

            if (
                child_return_code
                is not None
                and reader_finished
            ):
                break

            if (
                now - started_at
                > overall_timeout_seconds
            ):
                timeout_reason = (
                    "overall process timeout "
                    "exceeded"
                )

                break

            if (
                marker_seen_at is not None
                and (
                    now - marker_seen_at
                    > exit_grace_seconds
                )
            ):
                timeout_reason = (
                    "child process did not exit "
                    "within the grace period after "
                    "reporting success"
                )

                break

        if timeout_reason is not None:
            print(
                "",
                file=stream,
                flush=True,
            )

            print(
                "[monitor] ERROR: "
                f"{timeout_reason}.",
                file=stream,
                flush=True,
            )

            print(
                "[monitor] Terminating the "
                "child process tree.",
                file=stream,
                flush=True,
            )

            _terminate_process_tree(
                process
            )

            reader_thread.join(
                timeout=2.0
            )

            while True:
                try:
                    item = (
                        output_queue
                        .get_nowait()
                    )

                except queue.Empty:
                    break

                if isinstance(
                    item,
                    str,
                ):
                    print(
                        item,
                        end="",
                        file=stream,
                        flush=True,
                    )

                    marker_seen = (
                        marker_seen
                        or (
                            success_marker
                            in item
                        )
                    )

            return ProcessRunResult(
                child_return_code=(
                    process.poll()
                ),
                exit_code=EXIT_TIMEOUT,
                success_marker_seen=(
                    marker_seen
                ),
                timed_out=True,
                timeout_reason=(
                    timeout_reason
                ),
                elapsed_seconds=(
                    time.monotonic()
                    - started_at
                ),
            )

        child_return_code = (
            process.returncode
        )

        elapsed_seconds = (
            time.monotonic()
            - started_at
        )

        if child_return_code != 0:
            print(
                "[monitor] ERROR: Child "
                "process exited with code "
                f"{child_return_code}.",
                file=stream,
                flush=True,
            )

            return ProcessRunResult(
                child_return_code=(
                    child_return_code
                ),
                exit_code=(
                    child_return_code
                    if child_return_code > 0
                    else EXIT_MONITOR_FAILURE
                ),
                success_marker_seen=(
                    marker_seen
                ),
                timed_out=False,
                timeout_reason=None,
                elapsed_seconds=(
                    elapsed_seconds
                ),
            )

        if not marker_seen:
            print(
                "[monitor] ERROR: Child "
                "process exited cleanly, but "
                "the required success marker "
                "was not observed.",
                file=stream,
                flush=True,
            )

            return ProcessRunResult(
                child_return_code=(
                    child_return_code
                ),
                exit_code=(
                    EXIT_SUCCESS_MARKER_MISSING
                ),
                success_marker_seen=False,
                timed_out=False,
                timeout_reason=None,
                elapsed_seconds=(
                    elapsed_seconds
                ),
            )

        print(
            "[monitor] Child process "
            "exited cleanly after "
            f"{elapsed_seconds:.2f} "
            "seconds.",
            file=stream,
            flush=True,
        )

        return ProcessRunResult(
            child_return_code=(
                child_return_code
            ),
            exit_code=EXIT_SUCCESS,
            success_marker_seen=True,
            timed_out=False,
            timeout_reason=None,
            elapsed_seconds=(
                elapsed_seconds
            ),
        )

    except BaseException:
        _terminate_process_tree(
            process
        )

        raise

    finally:
        process.stdout.close()

        reader_thread.join(
            timeout=1.0
        )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run a Python module in a child "
            "process and fail if it does not "
            "really exit after reporting "
            "success."
        )
    )

    parser.add_argument(
        "--module",
        required=True,
        help=(
            "Python module executed with "
            "the current interpreter."
        ),
    )

    parser.add_argument(
        "--label",
        required=True,
        help=(
            "Human-readable process name."
        ),
    )

    parser.add_argument(
        "--success-marker",
        required=True,
        help=(
            "Exact output text proving that "
            "the smoke-test body completed."
        ),
    )

    parser.add_argument(
        "--overall-timeout-seconds",
        type=float,
        default=900.0,
        help=(
            "Maximum total child runtime. "
            "Default: 900 seconds."
        ),
    )

    parser.add_argument(
        "--exit-grace-seconds",
        type=float,
        default=30.0,
        help=(
            "Maximum time after the success "
            "marker before the child must "
            "exit. Default: 30 seconds."
        ),
    )

    return parser.parse_args()


def main() -> int:
    """Run the selected module under monitoring."""

    args = parse_args()

    project_root = (
        Path(__file__)
        .resolve()
        .parents[1]
    )

    result = run_monitored_process(
        [
            sys.executable,
            "-u",
            "-m",
            args.module,
        ],
        success_marker=(
            args.success_marker
        ),
        overall_timeout_seconds=(
            args.overall_timeout_seconds
        ),
        exit_grace_seconds=(
            args.exit_grace_seconds
        ),
        label=args.label,
        cwd=project_root,
    )

    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(
        main()
    )