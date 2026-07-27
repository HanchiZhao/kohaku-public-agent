from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest import TestCase

from scripts.run_process_with_timeout import (
    EXIT_SUCCESS,
    EXIT_SUCCESS_MARKER_MISSING,
    EXIT_TIMEOUT,
    run_monitored_process,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)

SUCCESS_MARKER = (
    "PROCESS_MONITOR_SUCCESS"
)


class ProcessTimeoutRunnerTests(
    TestCase
):
    """Protect real smoke-process shutdown behavior."""

    def test_clean_process_exit_passes(
        self,
    ) -> None:
        output = io.StringIO()

        result = run_monitored_process(
            [
                sys.executable,
                "-u",
                "-c",
                (
                    "print("
                    f"{SUCCESS_MARKER!r}, "
                    "flush=True)"
                ),
            ],
            success_marker=(
                SUCCESS_MARKER
            ),
            overall_timeout_seconds=5.0,
            exit_grace_seconds=1.0,
            label="clean-exit probe",
            cwd=PROJECT_ROOT,
            output_stream=output,
        )

        self.assertEqual(
            result.exit_code,
            EXIT_SUCCESS,
        )

        self.assertEqual(
            result.child_return_code,
            0,
        )

        self.assertTrue(
            result.success_marker_seen
        )

        self.assertFalse(
            result.timed_out
        )

    def test_zero_exit_without_marker_fails(
        self,
    ) -> None:
        output = io.StringIO()

        result = run_monitored_process(
            [
                sys.executable,
                "-u",
                "-c",
                (
                    "print("
                    "'different output', "
                    "flush=True)"
                ),
            ],
            success_marker=(
                SUCCESS_MARKER
            ),
            overall_timeout_seconds=5.0,
            exit_grace_seconds=1.0,
            label="missing-marker probe",
            cwd=PROJECT_ROOT,
            output_stream=output,
        )

        self.assertEqual(
            result.exit_code,
            EXIT_SUCCESS_MARKER_MISSING,
        )

        self.assertEqual(
            result.child_return_code,
            0,
        )

        self.assertFalse(
            result.success_marker_seen
        )

        self.assertFalse(
            result.timed_out
        )

    def test_process_hanging_after_success_marker_times_out(
        self,
    ) -> None:
        output = io.StringIO()

        result = run_monitored_process(
            [
                sys.executable,
                "-u",
                "-c",
                (
                    "import time; "
                    "print("
                    f"{SUCCESS_MARKER!r}, "
                    "flush=True); "
                    "time.sleep(30)"
                ),
            ],
            success_marker=(
                SUCCESS_MARKER
            ),
            overall_timeout_seconds=5.0,
            exit_grace_seconds=0.5,
            label=(
                "post-success hang probe"
            ),
            cwd=PROJECT_ROOT,
            output_stream=output,
        )

        self.assertEqual(
            result.exit_code,
            EXIT_TIMEOUT,
        )

        self.assertTrue(
            result.success_marker_seen
        )

        self.assertTrue(
            result.timed_out
        )

        self.assertIn(
            "grace period",
            result.timeout_reason or "",
        )

        self.assertLess(
            result.elapsed_seconds,
            10.0,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()