from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from kohakuterrarium import Studio


NAMESPACE_NAMES = (
    "sessions",
    "persistence",
    "attach",
    "identity",
    "catalog",
    "editors",
)


def public_member_names(value: Any) -> list[str]:
    """Return sorted public attribute names."""

    return sorted(
        name
        for name in dir(value)
        if not name.startswith("_")
    )


def describe_member(value: Any) -> str:
    """Return a safe, compact description of one member."""

    if callable(value):
        return "callable"

    return type(value).__name__


def print_namespace(
    namespace_name: str,
    namespace: Any,
) -> None:
    """Print public members of one Studio namespace."""

    print()
    print("=" * 72)
    print(f"Studio namespace: {namespace_name}")
    print("=" * 72)

    if namespace is None:
        print("Namespace is not available.")
        return

    for member_name in public_member_names(namespace):
        try:
            member = getattr(namespace, member_name)
            description = describe_member(member)
        except Exception as exception:
            description = (
                "unavailable: "
                f"{exception.__class__.__name__}"
            )

        print(
            f"{member_name:<36}"
            f"{description}"
        )


def print_nested_namespaces(
    parent_name: str,
    parent: Any,
    possible_names: Iterable[str],
) -> None:
    """Print selected nested namespace members when available."""

    for nested_name in possible_names:
        nested = getattr(
            parent,
            nested_name,
            None,
        )

        if nested is None:
            continue

        print_namespace(
            f"{parent_name}.{nested_name}",
            nested,
        )


async def main() -> None:
    """Inspect the public Studio persistence-related API."""

    print("=" * 72)
    print("KohakuTerrarium Studio persistence API inspection")
    print("=" * 72)

    async with Studio() as studio:
        for namespace_name in NAMESPACE_NAMES:
            namespace = getattr(
                studio,
                namespace_name,
                None,
            )

            print_namespace(
                namespace_name,
                namespace,
            )

            if namespace_name == "sessions":
                print_nested_namespaces(
                    namespace_name,
                    namespace,
                    (
                        "chat",
                        "history",
                        "lifecycle",
                        "search",
                    ),
                )

            if namespace_name == "persistence":
                print_nested_namespaces(
                    namespace_name,
                    namespace,
                    (
                        "sessions",
                        "history",
                        "store",
                    ),
                )

            if namespace_name == "attach":
                print_nested_namespaces(
                    namespace_name,
                    namespace,
                    (
                        "sessions",
                        "runtime",
                    ),
                )

    print()
    print("=" * 72)
    print("Inspection completed successfully")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())