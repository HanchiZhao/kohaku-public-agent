from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(
    __file__
).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


from app.persistence import (  # noqa: E402
    ACTIVE_STATUS,
    DELETED_STATUS,
    RECOVERY_FAILED_STATUS,
    ConversationRepository,
    Database,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect the public Agent SQLite registry "
            "and KohakuTerrarium session files."
        )
    )

    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help=(
            "Project root containing runtime/. "
            "Defaults to the current repository."
        ),
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Return exit code 1 when persistence "
            "inconsistencies are found."
        ),
    )

    return parser.parse_args()


def candidate_session_paths(
    *,
    session_root: Path,
    stored_value: str | None,
    creature_id: str | None,
    studio_session_id: str | None,
) -> list[Path]:
    values: list[str] = []

    for value in (
        stored_value,
        creature_id,
        studio_session_id,
    ):
        if value is None:
            continue

        normalized = str(value).strip()

        if (
            normalized
            and normalized not in values
        ):
            values.append(normalized)

    candidates: list[Path] = []

    for value in values:
        supplied = Path(
            value
        ).expanduser()

        if supplied.is_absolute():
            possible = [
                supplied,
            ]
        else:
            possible = [
                session_root / supplied,
            ]

        if supplied.suffix != ".kohakutr":
            possible.append(
                session_root
                / f"{supplied.name}.kohakutr"
            )

        for path in possible:
            resolved = path.resolve()

            if resolved not in candidates:
                candidates.append(
                    resolved
                )

    return candidates


def resolve_existing_path(
    *,
    session_root: Path,
    stored_value: str | None,
    creature_id: str | None,
    studio_session_id: str | None,
) -> Path | None:
    for candidate in candidate_session_paths(
        session_root=session_root,
        stored_value=stored_value,
        creature_id=creature_id,
        studio_session_id=studio_session_id,
    ):
        if candidate.is_file():
            return candidate

    return None


async def inspect_persistence(
    project_root: Path,
) -> dict[str, Any]:
    root = (
        project_root
        .expanduser()
        .resolve()
    )

    session_root = (
        root
        / "runtime"
        / "kohaku_sessions"
    ).resolve()

    database = Database(
        project_root=root
    )

    await database.start()

    try:
        repository = (
            ConversationRepository(
                database
            )
        )

        records = (
            await repository
            .list_conversations(
                owner_external_key=(
                    "development-user"
                ),
                include_deleted=True,
            )
        )

        active = 0
        recovery_failed = 0
        deleted = 0

        missing: list[dict[str, Any]] = []
        deleted_files_remaining: list[
            dict[str, Any]
        ] = []

        referenced_paths: set[Path] = set()
        conversation_rows: list[
            dict[str, Any]
        ] = []

        for record in records:
            if record.status == ACTIVE_STATUS:
                active += 1
            elif (
                record.status
                == RECOVERY_FAILED_STATUS
            ):
                recovery_failed += 1
            elif record.status == DELETED_STATUS:
                deleted += 1

            existing_path = (
                resolve_existing_path(
                    session_root=session_root,
                    stored_value=(
                        record
                        .studio_session_name
                    ),
                    creature_id=(
                        record.creature_id
                    ),
                    studio_session_id=(
                        record
                        .studio_session_id
                    ),
                )
            )

            candidates = (
                candidate_session_paths(
                    session_root=session_root,
                    stored_value=(
                        record
                        .studio_session_name
                    ),
                    creature_id=(
                        record.creature_id
                    ),
                    studio_session_id=(
                        record
                        .studio_session_id
                    ),
                )
            )

            if existing_path is not None:
                referenced_paths.add(
                    existing_path.resolve()
                )
            else:
                referenced_paths.update(
                    candidates
                )

            row_summary = {
                "public_id": (
                    record.public_id
                ),
                "title": record.title,
                "status": record.status,
                "session_file": (
                    existing_path.name
                    if existing_path
                    is not None
                    else None
                ),
                "session_file_exists": (
                    existing_path
                    is not None
                ),
                "recovery_error": (
                    record.recovery_error
                ),
            }

            conversation_rows.append(
                row_summary
            )

            if (
                record.status
                != DELETED_STATUS
                and existing_path is None
            ):
                missing.append(
                    row_summary
                )

            if (
                record.status
                == DELETED_STATUS
                and existing_path
                is not None
            ):
                deleted_files_remaining.append(
                    row_summary
                )

        actual_files: set[Path] = set()

        if session_root.is_dir():
            actual_files = {
                path.resolve()
                for path in session_root.glob(
                    "*.kohakutr"
                )
                if path.is_file()
            }

        orphan_files = sorted(
            str(path)
            for path in (
                actual_files
                - referenced_paths
            )
        )

        anomalies = (
            len(missing)
            + recovery_failed
            + len(orphan_files)
            + len(
                deleted_files_remaining
            )
        )

        return {
            "project_root": str(root),
            "database_url": (
                database.database_url
            ),
            "session_root": str(
                session_root
            ),
            "database_reachable": (
                await database.ping()
            ),
            "summary": {
                "total_conversations": (
                    len(records)
                ),
                "active": active,
                "recovery_failed": (
                    recovery_failed
                ),
                "deleted": deleted,
                "missing_session_files": (
                    len(missing)
                ),
                "orphan_session_files": (
                    len(orphan_files)
                ),
                "deleted_files_remaining": (
                    len(
                        deleted_files_remaining
                    )
                ),
                "anomalies": anomalies,
            },
            "conversations": (
                conversation_rows
            ),
            "missing_session_files": (
                missing
            ),
            "orphan_session_files": (
                orphan_files
            ),
            "deleted_files_remaining": (
                deleted_files_remaining
            ),
        }

    finally:
        await database.close()


def print_human_report(
    report: dict[str, Any],
) -> None:
    summary = report["summary"]

    print("=" * 72)
    print(
        "Kohaku Public Agent "
        "Persistence Doctor"
    )
    print("=" * 72)

    print(
        f"Project root: "
        f"{report['project_root']}"
    )

    print(
        f"Database: "
        f"{report['database_url']}"
    )

    print(
        f"Session root: "
        f"{report['session_root']}"
    )

    print(
        f"Database reachable: "
        f"{report['database_reachable']}"
    )

    print()
    print("Conversation summary")
    print("-" * 72)

    print(
        f"Total: "
        f"{summary['total_conversations']}"
    )
    print(
        f"Active: "
        f"{summary['active']}"
    )
    print(
        f"Recovery failed: "
        f"{summary['recovery_failed']}"
    )
    print(
        f"Deleted: "
        f"{summary['deleted']}"
    )
    print(
        f"Missing session files: "
        f"{summary['missing_session_files']}"
    )
    print(
        f"Orphan session files: "
        f"{summary['orphan_session_files']}"
    )
    print(
        f"Deleted rows with files remaining: "
        f"{summary['deleted_files_remaining']}"
    )

    print()
    print("Conversation records")
    print("-" * 72)

    rows = report["conversations"]

    if not rows:
        print("No conversation rows found.")
    else:
        for row in rows:
            print(
                f"- {row['public_id']} | "
                f"{row['status']} | "
                f"{row['title']}"
            )

            print(
                "  Session file: "
                + (
                    row["session_file"]
                    or "MISSING"
                )
            )

            if row["recovery_error"]:
                print(
                    "  Recovery error: "
                    f"{row['recovery_error']}"
                )

    orphan_files = report[
        "orphan_session_files"
    ]

    if orphan_files:
        print()
        print("Orphan session files")
        print("-" * 72)

        for path in orphan_files:
            print(f"- {path}")

    deleted_files = report[
        "deleted_files_remaining"
    ]

    if deleted_files:
        print()
        print(
            "Deleted conversations "
            "with files remaining"
        )
        print("-" * 72)

        for row in deleted_files:
            print(
                f"- {row['public_id']} | "
                f"{row['session_file']}"
            )

    print()
    print("=" * 72)

    if summary["anomalies"] == 0:
        print(
            "Persistence state is consistent."
        )
    else:
        print(
            "Persistence anomalies were found. "
            "This command did not modify anything."
        )

    print("=" * 72)


async def run() -> int:
    arguments = parse_arguments()

    report = await inspect_persistence(
        arguments.project_root
    )

    if arguments.json:
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_human_report(
            report
        )

    anomalies = int(
        report["summary"]["anomalies"]
    )

    if (
        arguments.strict
        and anomalies > 0
    ):
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(
        asyncio.run(
            run()
        )
    )