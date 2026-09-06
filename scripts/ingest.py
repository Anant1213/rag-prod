"""Idempotent ingest for Markdown and PDF.

    python -m scripts.ingest docs/

Re-running on unchanged files is a no-op; changed files are replaced in place.
PDF chunks carry the page they start on so citations can point at it.
"""
import hashlib
import pathlib
import re
import sys
from collections import Counter

import psycopg
from pgvector.psycopg import register_vector

from app.config import settings
from app.embeddings import embed_sync

# Bump when chunking or cleaning changes. It is folded into each document's
# version hash, so a pipeline change re-ingests everything instead of leaving
# stale chunks behind a byte-identical source file.
PIPELINE = "v2-paragraph-1100"

CHUNK_CHARS = 1100      # ~275 tokens, comfortably inside bge's 512-token window
OVERLAP = 200
MIN_CHUNK_CHARS = 120   # below this a fragment carries no retrievable signal

# Embed in batches. Handing fastembed a whole document at once let ONNX allocate
# for every chunk simultaneously: a 150-chunk PDF peaked at 3.6GB and was
# OOMKilled under a 2Gi container limit, while the same run on a laptop with no
# limit passed. Batching makes peak memory a function of this constant rather
# than of the largest document.
EMBED_BATCH = 32

# A line repeated across this fraction of a PDF's pages is furniture -- a running
# header, footer or watermark. Left in, it lands in every embedding from that
# document and pulls unrelated chunks toward each other.
BOILERPLATE_RATIO = 0.5
BOILERPLATE_MIN_PAGES = 10   # too few pages to tell furniture from real repetition

Segment = tuple[str, int | None]   # (text, 1-based page number or None for markdown)


# ---------------------------------------------------------------- loading

def _clean(text: str) -> str:
    text = text.replace("­", "")                 # soft hyphens
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)      # de-hyphenate across line breaks
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _boilerplate(pages: list[str]) -> set[str]:
    if len(pages) < BOILERPLATE_MIN_PAGES:
        return set()
    seen: Counter[str] = Counter()
    for raw in pages:
        # count each distinct line once per page, so a word repeated within one
        # page does not look like a running header
        seen.update({ln.strip() for ln in raw.splitlines() if ln.strip()})
    threshold = len(pages) * BOILERPLATE_RATIO
    return {ln for ln, n in seen.items() if n >= threshold and len(ln) < 120}


def _pdf_segments(path: pathlib.Path) -> list[Segment]:
    from pypdf import PdfReader

    pages = [(p.extract_text() or "") for p in PdfReader(str(path)).pages]
    furniture = _boilerplate(pages)

    segments: list[Segment] = []
    for number, raw in enumerate(pages, start=1):
        kept = [ln for ln in raw.splitlines() if ln.strip() not in furniture]
        body = _clean("\n".join(kept))
        if body:
            segments.append((body, number))
    return segments


def _md_segments(path: pathlib.Path) -> list[Segment]:
    return [(_clean(path.read_text(encoding="utf-8")), None)]


LOADERS = {".pdf": _pdf_segments, ".md": _md_segments}


# ---------------------------------------------------------------- chunking

def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _units(segments: list[Segment]) -> list[Segment]:
    """Break segments into pieces that each fit in a chunk, preserving page numbers.

    Splitting on paragraphs first, then sentences, keeps semantically whole ideas
    together; the old word-window chunker cut mid-sentence and embedded fragments.
    """
    units: list[Segment] = []
    for text, page in segments:
        for para in _paragraphs(text):
            if len(para) <= CHUNK_CHARS:
                units.append((para, page))
                continue
            for sent in _sentences(para):
                if len(sent) <= CHUNK_CHARS:
                    units.append((sent, page))
                else:  # a single unbroken run, e.g. a table dumped as one line
                    for i in range(0, len(sent), CHUNK_CHARS):
                        units.append((sent[i:i + CHUNK_CHARS], page))
    return units


def chunk_segments(segments: list[Segment]) -> list[Segment]:
    """Pack units up to CHUNK_CHARS, tagging each chunk with the page it starts on."""
    packed: list[Segment] = []
    buf: list[str] = []
    start_page: int | None = None
    size = 0

    for piece, page in _units(segments):
        if buf and size + len(piece) > CHUNK_CHARS:
            packed.append(("\n\n".join(buf), start_page))
            buf, size, start_page = [], 0, None
        if start_page is None:
            start_page = page
        buf.append(piece)
        size += len(piece) + 2
    if buf:
        packed.append(("\n\n".join(buf), start_page))

    # Overlap: carry the tail of the previous chunk so an idea split across a
    # boundary is still retrievable from either side.
    out: list[Segment] = []
    for i, (body, page) in enumerate(packed):
        if i and OVERLAP:
            body = packed[i - 1][0][-OVERLAP:] + "\n\n" + body
        if len(body) >= MIN_CHUNK_CHARS or len(packed) == 1:
            out.append((body, page))
    return out


# ---------------------------------------------------------------- ingest

def main(root: str, tenant: str = "default") -> None:
    paths = sorted(p for p in pathlib.Path(root).rglob("*") if p.suffix.lower() in LOADERS)
    if not paths:
        sys.exit(f"no .md or .pdf files under {root}")

    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        register_vector(conn)
        for path in paths:
            doc_id = str(path)
            # hash the bytes (not decoded text, so PDFs work) together with the
            # pipeline id, so either a new source or a new chunker forces a rebuild
            digest = hashlib.sha256(path.read_bytes())
            digest.update(PIPELINE.encode())
            version = digest.hexdigest()[:12]

            row = conn.execute(
                "select version from chunks where tenant=%s and doc_id=%s limit 1",
                (tenant, doc_id),
            ).fetchone()
            if row and row[0] == version:
                print(f"skip   {doc_id} (unchanged)")
                continue

            pieces = chunk_segments(LOADERS[path.suffix.lower()](path))
            if not pieces:
                print(f"warn   {doc_id} produced no text (scanned images?) -- skipped")
                continue

            # replace-in-place: delete then insert, so removed content leaves no orphan vectors
            conn.execute("delete from chunks where tenant=%s and doc_id=%s", (tenant, doc_id))
            for start in range(0, len(pieces), EMBED_BATCH):
                batch = pieces[start : start + EMBED_BATCH]
                vectors = embed_sync([text for text, _ in batch])
                with conn.cursor() as cur:
                    cur.executemany(
                        """insert into chunks
                           (tenant, doc_id, source, version, chunk_index, text, page, embedding)
                           values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        [
                            (tenant, doc_id, path.name, version, start + i, text, page, vec)
                            for i, ((text, page), vec) in enumerate(zip(batch, vectors))
                        ],
                    )
            span = f" pages 1-{max(p for _, p in pieces if p)}" if pieces[0][1] else ""
            print(f"ingest {doc_id} -> {len(pieces)} chunks{span} @ {version}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs")
