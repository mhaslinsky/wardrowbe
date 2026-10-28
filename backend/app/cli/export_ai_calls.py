import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from app.database import async_session_maker, engine
from app.services.ai_call_query_service import AICallQueryService, parse_cursor


async def export_calls(output: Path) -> int:
    """Write all users' call records to a new private JSONL file."""
    created = False
    count = 0
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            os.fchmod(destination.fileno(), 0o600)
            async with async_session_maker() as db:
                service = AICallQueryService(db)
                cursor = None
                while True:
                    page = await service.page(None, 100, cursor)
                    for call in page.items:
                        destination.write(
                            json.dumps(
                                {"schema_version": 1, **call.model_dump(mode="json")},
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        count += 1
                    if page.next_cursor is None:
                        break
                    cursor = parse_cursor(page.next_cursor)
        return count
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    finally:
        await engine.dispose()


def main() -> int:
    """Export to the required path, reporting a count or a nonzero failure."""
    parser = argparse.ArgumentParser(description="Export AI calls and linked feedback")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        count = asyncio.run(export_calls(args.output))
    except Exception as error:
        print(f"AI call export failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(json.dumps({"count": count, "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
