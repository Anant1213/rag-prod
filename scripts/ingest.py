"""Idempotent ingest. Re-running on unchanged docs is a no-op; changed docs are replaced.

    python -m scripts.ingest docs/
"""
import hashlib
import pathlib
import sys

import psycopg
from pgvector.psycopg import register_vector

from app.config import settings
from app.embeddings import embed_sync

CHUNK_CHARS = 900
OVERLAP = 150


def chunk_text(text: str) -> list[str]:
    words, chunks, buf, size = text.split(), [], [], 0
    for w in words:
        buf.append(w)
        size += len(w) + 1
        if size >= CHUNK_CHARS:
            chunks.append(" ".join(buf))
            keep = " ".join(buf)[-OVERLAP:].split()
            buf, size = keep, sum(len(x) + 1 for x in keep)
    if buf:
        chunks.append(" ".join(buf))
    return [c for c in chunks if c.strip()]


def main(root: str, tenant: str = "default") -> None:
    paths = sorted(pathlib.Path(root).rglob("*.md"))
    if not paths:
        sys.exit(f"no .md files under {root}")

    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        register_vector(conn)
        for path in paths:
            body = path.read_text(encoding="utf-8")
            doc_id = str(path)
            version = hashlib.sha256(body.encode()).hexdigest()[:12]

            row = conn.execute(
                "select version from chunks where tenant=%s and doc_id=%s limit 1",
                (tenant, doc_id),
            ).fetchone()
            if row and row[0] == version:
                print(f"skip   {doc_id} (unchanged)")
                continue

            # replace-in-place: delete then insert, so removed content leaves no orphan vectors
            conn.execute("delete from chunks where tenant=%s and doc_id=%s", (tenant, doc_id))
            pieces = chunk_text(body)
            vectors = embed_sync(pieces)
            with conn.cursor() as cur:
                cur.executemany(
                    """insert into chunks
                       (tenant, doc_id, source, version, chunk_index, text, embedding)
                       values (%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (tenant, doc_id, path.name, version, i, piece, vec)
                        for i, (piece, vec) in enumerate(zip(pieces, vectors))
                    ],
                )
            print(f"ingest {doc_id} -> {len(pieces)} chunks @ {version}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs")
