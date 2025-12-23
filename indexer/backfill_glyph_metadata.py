#!/usr/bin/env python3
import os
import sys
import datetime
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.session import IndexerSessionLocal
from database.models import BackfillStatus

BATCH_SIZE = int(os.getenv("GLYPH_METADATA_BACKFILL_BATCH_SIZE", "5000"))
BACKFILL_TYPE = "glyph_metadata"


def _get_or_create_status(db):
    status = db.query(BackfillStatus).filter(BackfillStatus.backfill_type == BACKFILL_TYPE).first()
    if status is None:
        status = BackfillStatus(
            backfill_type=BACKFILL_TYPE,
            is_complete=False,
            last_processed_id=0,
            total_processed=0,
            started_at=datetime.datetime.utcnow(),
        )
        db.add(status)
        db.commit()
    return status


def main():
    db = IndexerSessionLocal()
    try:
        status = _get_or_create_status(db)
        if status.is_complete:
            return

        last_id = int(status.last_processed_id or 0)

        while True:
            rows = db.execute(
                text(
                    """
                    SELECT id, ref, token_type, name, ticker, description, author, container, embed_type, embed_data, remote_url
                    FROM glyphs
                    WHERE id > :last_id
                    ORDER BY id ASC
                    LIMIT :limit
                    """
                ),
                {"last_id": last_id, "limit": BATCH_SIZE},
            ).fetchall()

            if not rows:
                status.is_complete = True
                status.completed_at = datetime.datetime.utcnow()
                db.add(status)
                db.commit()
                return

            updated = 0

            for r in rows:
                glyph_id = int(r.id)
                ref = r.ref

                name = (r.name or "").strip()
                ticker = (r.ticker or "").strip() if r.ticker is not None else ""
                description = (r.description or "").strip()
                author = (r.author or "").strip()
                container = (r.container or "").strip()

                is_placeholder_name = name.lower() in {"unnamed", "unnamed token"}
                needs_meta = (
                    name == "" or is_placeholder_name or
                    (ticker == "") or
                    (description == "") or
                    (author == "") or
                    (container == "")
                )

                has_image = bool(r.embed_data) or bool(r.remote_url)
                needs_image = not has_image

                if not (needs_meta or needs_image):
                    last_id = glyph_id
                    continue

                src = db.execute(
                    text(
                        """
                        SELECT
                            name,
                            ticker,
                            description,
                            author,
                            container,
                            icon_mime_type,
                            icon_data,
                            icon_url
                        FROM glyph_tokens
                        WHERE token_id = :ref
                          AND (name IS NOT NULL OR ticker IS NOT NULL OR description IS NOT NULL OR icon_data IS NOT NULL OR icon_url IS NOT NULL)
                        ORDER BY updated_at DESC NULLS LAST, id DESC
                        LIMIT 1
                        """
                    ),
                    {"ref": ref},
                ).mappings().first()

                if src is None:
                    src = db.execute(
                        text(
                            """
                            SELECT
                                name,
                                ticker,
                                description,
                                author,
                                container,
                                icon_mime_type,
                                icon_data,
                                icon_url
                            FROM nfts
                            WHERE token_id = :ref
                              AND (name IS NOT NULL OR ticker IS NOT NULL OR description IS NOT NULL OR icon_data IS NOT NULL OR icon_url IS NOT NULL)
                            ORDER BY updated_at DESC NULLS LAST, id DESC
                            LIMIT 1
                            """
                        ),
                        {"ref": ref},
                    ).mappings().first()

                tf = None
                if needs_image:
                    tf = db.execute(
                        text(
                            """
                            SELECT file_key, mime_type, file_data, remote_url
                            FROM token_files
                            WHERE token_id = :ref
                            ORDER BY
                                CASE
                                    WHEN file_key = 'icon' THEN 0
                                    WHEN file_key = 'image' THEN 1
                                    WHEN file_key = 'main' THEN 2
                                    ELSE 9
                                END,
                                id DESC
                            LIMIT 1
                            """
                        ),
                        {"ref": ref},
                    ).mappings().first()

                new_name = None
                new_ticker = None
                new_description = None
                new_author = None
                new_container = None
                new_embed_type = None
                new_embed_data = None
                new_remote_url = None

                if src is not None:
                    if (name == "" or is_placeholder_name) and src.get("name"):
                        new_name = str(src.get("name"))
                    if ticker == "" and src.get("ticker"):
                        new_ticker = str(src.get("ticker"))
                    if description == "" and src.get("description"):
                        new_description = str(src.get("description"))
                    if author == "" and src.get("author"):
                        new_author = str(src.get("author"))
                    if container == "" and src.get("container"):
                        new_container = str(src.get("container"))

                    if needs_image:
                        if src.get("icon_data"):
                            new_embed_data = str(src.get("icon_data"))
                            new_embed_type = str(src.get("icon_mime_type") or "") or None
                        elif src.get("icon_url"):
                            new_remote_url = str(src.get("icon_url"))

                if needs_image and tf is not None:
                    if not new_embed_data and tf.get("file_data"):
                        new_embed_data = str(tf.get("file_data"))
                        new_embed_type = str(tf.get("mime_type") or "") or None
                    if not new_remote_url and tf.get("remote_url"):
                        new_remote_url = str(tf.get("remote_url"))

                if (name != "" and not is_placeholder_name) and new_name is None:
                    new_name = None

                if new_name is None and is_placeholder_name:
                    new_name = ""

                if (
                    new_name is None and
                    new_ticker is None and
                    new_description is None and
                    new_author is None and
                    new_container is None and
                    new_embed_type is None and
                    new_embed_data is None and
                    new_remote_url is None
                ):
                    last_id = glyph_id
                    continue

                db.execute(
                    text(
                        """
                        UPDATE glyphs SET
                            name = COALESCE(:name, name),
                            ticker = COALESCE(:ticker, ticker),
                            description = COALESCE(:description, description),
                            author = COALESCE(:author, author),
                            container = COALESCE(:container, container),
                            embed_type = COALESCE(:embed_type, embed_type),
                            embed_data = COALESCE(:embed_data, embed_data),
                            remote_url = COALESCE(:remote_url, remote_url),
                            updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": glyph_id,
                        "name": new_name,
                        "ticker": new_ticker,
                        "description": new_description,
                        "author": new_author,
                        "container": new_container,
                        "embed_type": new_embed_type,
                        "embed_data": new_embed_data,
                        "remote_url": new_remote_url,
                    },
                )
                updated += 1
                last_id = glyph_id

            status.last_processed_id = last_id
            status.total_processed = int(status.total_processed or 0) + updated
            status.updated_at = datetime.datetime.utcnow()
            db.add(status)
            db.commit()

    finally:
        db.close()


if __name__ == "__main__":
    main()
