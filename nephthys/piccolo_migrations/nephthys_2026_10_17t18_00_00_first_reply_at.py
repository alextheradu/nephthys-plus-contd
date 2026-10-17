from nephthys.database.raw_migration import raw_migration

ID = "2026-10-17T18:00:00:000000"
VERSION = "1.35.0"
DESCRIPTION = "Track when a ticket first got a reply"


async def forwards():
    return raw_migration(
        migration_id=ID,
        app_name="nephthys",
        description=DESCRIPTION,
        forwards="""
ALTER TABLE "Ticket" ADD COLUMN "firstReplyAt" TIMESTAMPTZ;
UPDATE "Ticket" SET "firstReplyAt" = "assignedAt";
""",
        backwards="""
ALTER TABLE "Ticket" DROP COLUMN "firstReplyAt";
""",
    )
