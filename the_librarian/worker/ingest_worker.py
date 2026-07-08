"""
worker/ingest_worker.py
────────────────────────────────────────────────────────────────────────
Standalone OCR + embedding worker for Icarus's PDF archive.

Runs entirely outside Django. Polls production for pending ArchiveIssues,
downloads each PDF, OCRs + chunks + embeds it locally, and POSTs the
finished chunks back in batches for Postgres insertion.

Two ways to run it:

1. Browser control panel (no terminal knowledge needed beyond starting it):
       python worker_ui.py
   Opens a local page where you paste the URL and API key, click Start,
   watch progress, click Stop whenever. See worker_ui.py.

2. Command line, configured via environment variables:
       export ICARUS_BASE_URL="https://kadannal-koodu.miku-brill.ts.net"
       export ICARUS_WORKER_TOKEN="<a key generated from the Remote
                                    Ingestion Worker dashboard panel>"
       python worker_ingest.py
       python worker_ingest.py --force   # re-ingest everything

Before either one, an admin must:
    1. Generate an API key from the "Remote Ingestion Worker" dashboard
       panel and copy it (shown in plaintext exactly once).
    2. Click "Enable Remote Ingestion" on the same panel — every request
       403s while this switch is off, regardless of token. It stays
       enabled until an admin disables it again; there is no automatic
       timeout.

Requires the_librarian/services/{ocr_processing.py,yolo_segmentation.py,
text_cleanup.py} to be importable — normally satisfied by running this
from a full checkout of the Icarus repo, from inside the worker/ folder.
"""
import argparse
import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path

import requests
from pdf2image import convert_from_path, pdfinfo_from_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest_worker")

# Read at import time as *optional* defaults for CLI use — NOT required,
# since the browser control panel (worker_ui.py) supplies these per-run
# from a form instead. Only main() (the CLI entrypoint below) enforces
# that they're actually present.
BASE_URL = os.environ.get("ICARUS_BASE_URL")
TOKEN = os.environ.get("ICARUS_WORKER_TOKEN")
EMBEDDING_MODEL = os.environ.get("LIBRARIAN_EMBEDDING_MODEL", "sentence-transformers/LaBSE")
CACHE_DIR = Path(os.environ.get("WORKER_OCR_CACHE", "./ocr_cache"))
WORKER_BATCH_SIZE = 150  # stays comfortably under the server's MAX_CHUNKS_PER_BATCH=200

# Add the repo's project root to sys.path so we can import the Django-free
# service modules directly without configuring Django at all.
REPO_ROOT = os.environ.get("ICARUS_REPO_ROOT", "..")
sys.path.insert(0, REPO_ROOT)

from the_librarian.services.ocr_processing import process_page          # noqa: E402
from the_librarian.services.text_cleanup import chunk_pages             # noqa: E402


class StopRequested(Exception):
    """Raised internally to unwind cleanly out of an in-progress run."""


_model = None


def get_model(model_name):
    """
    Module-level cache (not per-runner) so that starting, stopping, and
    starting the worker again in the same process — the normal click
    pattern in the browser control panel — doesn't reload the embedding
    model from scratch every time.
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model: %s", model_name)
        _model = SentenceTransformer(model_name, device="cpu")
    return _model


def embed_texts(texts, model_name):
    return get_model(model_name).encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


def _page_cache_path(cache_dir, pdf_stem, page_num):
    d = cache_dir / pdf_stem
    d.mkdir(parents=True, exist_ok=True)
    return d / f"page_{page_num}.txt"


class WorkerRunner:
    """
    Everything needed for one worker run against one Icarus server, bundled
    so it can be constructed fresh per-run with base_url/token/stop_event
    supplied by whichever caller is driving it — the CLI entrypoint below,
    or worker_ui.py's Flask routes.

    stop_event is checked at page, chunk-download, embedding, and
    batch-submit boundaries — the same granularity as is_stop_requested()
    in the_librarian/services/ingestion.py's local pipeline, so "Stop"
    finishes the current unit of work rather than cutting off mid-page.
    """

    def __init__(self, base_url, token, *, embedding_model=None, cache_dir=None,
                 batch_size=WORKER_BATCH_SIZE, stop_event=None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.embedding_model = embedding_model or EMBEDDING_MODEL
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.batch_size = batch_size
        self.stop_event = stop_event or threading.Event()

    def _check_stop(self):
        if self.stop_event.is_set():
            raise StopRequested()

    def ocr_pdf(self, pdf_path: Path):
        """Returns (list of (page_number, text), total_pages). Resumes from a
        local per-page cache so a crashed or stopped run doesn't re-OCR
        pages already done."""
        pdf_info = pdfinfo_from_path(str(pdf_path))
        total_pages = pdf_info["Pages"]
        stem = pdf_path.stem

        page_texts = []
        for page_num in range(1, total_pages + 1):
            self._check_stop()
            cache_file = _page_cache_path(self.cache_dir, stem, page_num)
            if cache_file.exists():
                logger.info("  page %d/%d (cached)", page_num, total_pages)
                page_texts.append((page_num, cache_file.read_text(encoding="utf-8")))
                continue

            logger.info("  OCR page %d/%d", page_num, total_pages)
            images = convert_from_path(str(pdf_path), first_page=page_num, last_page=page_num, dpi=300)
            text = process_page(images[0])
            cache_file.write_text(text, encoding="utf-8")
            page_texts.append((page_num, text))

        return page_texts, total_pages

    def fetch_pending(self, force=False):
        resp = requests.get(f"{self.base_url}/librarian/api/worker/pending/",
                             headers=self.headers, params={"force": str(force).lower()}, timeout=30)
        if resp.status_code == 403:
            logger.error("Rejected fetching pending list: %s", resp.text)
            return []
        resp.raise_for_status()
        return resp.json()["pending"]

    def process_one(self, item, force=False):
        title = item["title"]
        logger.info("Processing: %s (year=%s month=%s)", title, item.get("year"), item.get("month"))

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / f"{item['pk']}.pdf"
            with requests.get(item["download_url"], headers=self.headers, stream=True, timeout=120) as r:
                if r.status_code == 403:
                    logger.warning("Download rejected (remote ingestion likely disabled, or key revoked): %s", r.text)
                    return
                r.raise_for_status()
                with open(pdf_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        self._check_stop()
                        f.write(chunk)

            page_texts, total_pages = self.ocr_pdf(pdf_path)
            chunks = chunk_pages(page_texts)
            if not chunks:
                logger.warning("  No usable text extracted from %s — skipping submit.", title)
                return

            self._check_stop()
            embeddings = embed_texts([c["chunk_text"] for c in chunks], self.embedding_model)
            for c, e in zip(chunks, embeddings):
                c["embedding"] = e

            batches = [chunks[i:i + self.batch_size] for i in range(0, len(chunks), self.batch_size)]
            for batch_index, batch in enumerate(batches):
                self._check_stop()
                payload = {
                    "archive_issue_pk": item["pk"],
                    "batch_index": batch_index,
                    "is_final": batch_index == len(batches) - 1,
                    "total_pages": total_pages,
                    "chunks": batch,
                }
                resp = requests.post(
                    f"{self.base_url}/librarian/api/worker/submit/",
                    headers={**self.headers, "Content-Type": "application/json"},
                    data=json.dumps(payload), timeout=120,
                )
                if resp.status_code == 403:
                    logger.warning("Submit rejected mid-run (disabled or key revoked): %s", resp.text)
                    return
                resp.raise_for_status()
                logger.info("  Batch %d/%d submitted: %s", batch_index + 1, len(batches), resp.json())

    def run(self, force=False):
        """Runs one full pass over all pending issues. Returns a summary dict:
        {"processed": int, "failed": int, "stopped": bool}."""
        processed, failed, stopped = 0, 0, False
        try:
            pending = self.fetch_pending(force=force)
            logger.info("%d issue(s) to process", len(pending))
            for item in pending:
                self._check_stop()
                try:
                    self.process_one(item, force=force)
                    processed += 1
                except StopRequested:
                    raise
                except Exception:
                    logger.exception("Failed on %s", item.get("title"))
                    failed += 1
        except StopRequested:
            stopped = True
            logger.info("Stopped by request.")
        return {"processed": processed, "failed": failed, "stopped": stopped}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-ingest everything, including already-linked issues")
    args = parser.parse_args()

    if not BASE_URL or not TOKEN:
        parser.error(
            "ICARUS_BASE_URL and ICARUS_WORKER_TOKEN must be set as environment "
            "variables for command-line use. Prefer not dealing with environment "
            "variables? Run `python worker_ui.py` instead and paste them into "
            "the form that opens in your browser."
        )

    runner = WorkerRunner(
        BASE_URL, TOKEN,
        embedding_model=EMBEDDING_MODEL, cache_dir=CACHE_DIR, batch_size=WORKER_BATCH_SIZE,
    )
    summary = runner.run(force=args.force)
    logger.info(
        "Done — processed %d, failed %d%s",
        summary["processed"], summary["failed"],
        " (stopped early)" if summary["stopped"] else "",
    )


if __name__ == "__main__":
    main()