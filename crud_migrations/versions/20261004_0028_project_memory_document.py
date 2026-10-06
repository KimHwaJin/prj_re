"""Collapse project topic collections into one Markdown Store document.

Run migrations with old application writers stopped. Topic API/replay receipts
are a versioned contract boundary. Unrelated Store namespaces stay untouched.
Downgrade stores the whole document as a marked legacy topic, preserving text
for upgrade; old-code edits of that topic are ordinary legacy updates.
"""

from itertools import groupby
import json
import sqlalchemy as sa
from alembic import op

revision = "20261004_0028"
down_revision = "20261003_0027"
branch_labels = None
depends_on = None
TITLES = {
    "background": "프로젝트 배경",
    "analysis_preferences": "분석 선호",
    "report_preferences": "보고서 선호",
    "shared_findings": "공유할 주요 발견",
}
MAX_CHARS = 1_000_000


def clear_receipts(conn):
    # A topic-write receipt cannot be interpreted as a document-write receipt.
    conn.execute(
        sa.text("""DELETE FROM store s USING projects p
        WHERE s.prefix = 'dtest.project_memory_receipts.' || p.user_id::text || '.' || p.project_id::text""")
    )


def upgrade():
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("""SELECT s.prefix,s.key,s.value,s.created_at,s.updated_at,
        p.user_id,p.project_id FROM store s JOIN projects p
        ON starts_with(s.prefix, 'dtest.project_memory.' || p.user_id::text || '.' || p.project_id::text || '.')
        ORDER BY p.user_id,p.project_id,s.prefix,s.key""")
    ).mappings()
    # Groups are processed per project, never as one all-projects document.
    for (user_id, project_id), group in groupby(
        rows, lambda row: (row["user_id"], row["project_id"])
    ):
        topics = list(group)
        prefix = "dtest.project_memory." + str(user_id) + "." + str(project_id)
        if conn.execute(
            sa.text(
                "SELECT 1 FROM store WHERE prefix=:prefix AND key='document'"
            ),
            {"prefix": prefix},
        ).first():
            raise RuntimeError(
                "Both topic and document project memories exist; "
                "stop old writers and resolve before "
                "migration"
            )
        for row in topics:
            value = row["value"]
            section = row["prefix"].split(".")[-1]
            if (
                row["prefix"] != prefix + "." + section
                or section not in TITLES
                or value.get("section") != section
                or value.get("key") != row["key"]
            ):
                raise RuntimeError(
                    "Invalid legacy memory identity; migration must "
                    "not silently discard "
                    "it"
                )
            if (
                not isinstance(value.get("content"), str)
                or type(value.get("version")) is not int
                or value["version"] < 1
            ):
                raise RuntimeError("Invalid legacy project memory document")
        if (
            len(topics) == 1
            and topics[0]["value"].get("document_schema_version") == 2
        ):
            value = topics[0]["value"]
            document = {
                k: value[k]
                for k in ("content", "version", "updated_at", "source")
            }
            document["schema_version"] = 2
            document["source"] = value.get("document_source", value["source"])
        else:
            sections = []
            for section, title in TITLES.items():
                bodies = [
                    row["value"]["content"]
                    for row in topics
                    if row["value"]["section"] == section
                    and not row["value"].get("is_deleted")
                ]
                if bodies:
                    sections.append("## " + title + "\n" + "\n\n".join(bodies))
            content = "\n\n".join(sections)
            if content:
                content += "\n"
            document = {
                "schema_version": 2,
                "content": content,
                "version": sum(row["value"]["version"] for row in topics),
                "updated_at": max(
                    row["updated_at"] for row in topics
                ).isoformat(),
                "source": {
                    "kind": "migration",
                    "from_schema_version": 1,
                    "previous_sources": [
                        {
                            k: row["value"].get(k)
                            for k in (
                                "section",
                                "key",
                                "version",
                                "is_deleted",
                                "source",
                                "updated_at",
                            )
                        }
                        for row in topics
                    ],
                },
            }
        if len(document["content"]) > MAX_CHARS:
            raise RuntimeError(
                "Migrated memory exceeds the absolute content "
                "limit; shorten it explicitly "
                "first"
            )
        conn.execute(
            sa.text("""INSERT INTO store(prefix,key,value,created_at,updated_at)
            VALUES(:prefix,'document',CAST(:value AS jsonb),:created,:updated)"""),
            {
                "prefix": prefix,
                "value": json.dumps(document, ensure_ascii=False),
                "created": min(row["created_at"] for row in topics),
                "updated": max(row["updated_at"] for row in topics),
            },
        )
        for row in topics:
            conn.execute(
                sa.text("DELETE FROM store WHERE prefix=:prefix AND key=:key"),
                {"prefix": row["prefix"], "key": row["key"]},
            )
    clear_receipts(conn)


def downgrade():
    conn = op.get_bind()
    rows = (
        conn.execute(
            sa.text("""SELECT s.* FROM store s JOIN projects p
        ON s.prefix = 'dtest.project_memory.' || p.user_id::text || '.' || p.project_id::text
        WHERE s.key='document'""")
        )
        .mappings()
        .all()
    )
    for row in rows:
        value = row["value"]
        topic = {
            "section": "background",
            "key": "document",
            "content": value["content"],
            "version": value["version"],
            "is_deleted": False,
            "source": value["source"]
            if value["source"].get("kind") in ("user_edit", "user_request")
            else {"kind": "user_edit"},
            "document_source": value["source"],
            "updated_at": value["updated_at"],
            "document_schema_version": 2,
        }
        conn.execute(
            sa.text("""INSERT INTO store(prefix,key,value,created_at,updated_at)
            VALUES(:prefix,'document',CAST(:value AS jsonb),:created,:updated)"""),
            {
                "prefix": row["prefix"] + ".background",
                "value": json.dumps(topic, ensure_ascii=False),
                "created": row["created_at"],
                "updated": row["updated_at"],
            },
        )
        conn.execute(
            sa.text("DELETE FROM store WHERE prefix=:prefix AND key=:key"),
            {"prefix": row["prefix"], "key": row["key"]},
        )
    clear_receipts(conn)
