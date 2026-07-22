import asyncio
from pathlib import Path

from kohakuterrarium import Studio


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = PROJECT_ROOT / "runtime" / "workspaces" / "studio-smoke"

CREATURE_REF = "@kohaku-public-agent-lab/creatures/public-assistant"


async def main() -> None:
    """Verify that Studio can start the public assistant and stream a response."""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    print("Starting KohakuTerrarium Studio...")

    async with Studio() as studio:
        session = await studio.sessions.start_creature(
            CREATURE_REF,
            pwd=str(WORKSPACE_DIR),
            name="studio-smoke-test",
        )

        if not session.creatures:
            raise RuntimeError("Studio created a session without a creature.")

        session_id = session.session_id
        creature_id = session.creatures[0]["creature_id"]

        print(f"Session ID: {session_id}")
        print(f"Creature ID: {creature_id}")
        print("Assistant: ", end="", flush=True)

        try:
            async for chunk in studio.sessions.chat.chat(
                session_id,
                creature_id,
                (
                    "请用中文介绍你的身份，并说明当前是否已经接入"
                    "正式网站、真实用户账号、文件上传和数据库。"
                ),
            ):
                print(chunk, end="", flush=True)

            print()

        finally:
            print("Stopping session...")
            await studio.sessions.stop(session_id)

    print("Studio closed successfully.")


if __name__ == "__main__":
    asyncio.run(main())