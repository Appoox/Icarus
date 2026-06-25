"""
Django-Q2 async task wrappers for The Librarian.

Each function is a thin, pickle-friendly wrapper around a service function.
Arguments are always primitive PKs — never model instances — to satisfy
Django-Q2's ORM broker serialisation requirements.

Usage (queue from anywhere):
    from django_q.tasks import async_task
    async_task('the_librarian.tasks.async_ingest_archive_issue', archive_issue_pk)

The qcluster management command must be running for tasks to execute:
    python manage.py qcluster
"""
import logging

logger = logging.getLogger(__name__)


def async_ingest_archive_issue(archive_issue_pk, force=False):
    """
    Queue-compatible wrapper: ingest the PDF attached to a single ArchiveIssue.

    Checks the stop-signal at entry so that tasks queued before a user-requested
    stop are discarded cleanly rather than processing mid-batch.
    """
    from the_librarian.services.ingestion import ingest_archive_issue, is_stop_requested

    if is_stop_requested():
        logger.info(
            "Q2 task async_ingest_archive_issue pk=%s skipped — stop requested",
            archive_issue_pk,
        )
        return {
            "status": "skipped",
            "message": "Stopped by user request",
            "filename": f"ArchiveIssue pk={archive_issue_pk}",
            "chunks_created": 0,
        }

    logger.info("Q2 task: ingest_archive_issue pk=%s force=%s", archive_issue_pk, force)
    return ingest_archive_issue(archive_issue_pk, force=force)


def async_ingest_article(article_pk):
    """Queue-compatible wrapper: (re)index a single live Article page."""
    from the_librarian.services.indexing import index_article

    logger.info("Q2 task: index_article pk=%s", article_pk)
    return index_article(article_pk)


def async_ingest_author(author_pk):
    """Queue-compatible wrapper: (re)index a single Literati author profile."""
    from the_librarian.services.indexing import index_author

    logger.info("Q2 task: index_author pk=%s", author_pk)
    return index_author(author_pk)


def async_index_editorial(issue_pk):
    """Queue-compatible wrapper: (re)index the editorial StreamField of an Issue."""
    from the_librarian.services.indexing import index_editorial

    logger.info("Q2 task: index_editorial pk=%s", issue_pk)
    return index_editorial(issue_pk)
