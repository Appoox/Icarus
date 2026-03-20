from django.shortcuts import render
from django.http import JsonResponse, Http404
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.http import require_GET, require_POST

import json
import logging
from pathlib import Path

from the_librarian.models import ArchiveDocument, DocumentChunk
from the_librarian.services.search import search_similar, search_by_document

logger = logging.getLogger(__name__)


# ── Search views ──────────────────────────────────────────────────────────

def search_view(request):
    """Render the search page with optional results."""
    query = request.GET.get("q", "").strip()
    top_k = int(request.GET.get("top_k", 5))
    results = []

    if query:
        results = search_similar(query, top_k=top_k)

    return render(request, "the_librarian/search.html", {
        "query": query,
        "results": results,
        "top_k": top_k,
    })


def search_api(request):
    """JSON API for similarity search (for AJAX calls)."""
    query = request.GET.get("q", "").strip()
    top_k = int(request.GET.get("top_k", 5))
    document = request.GET.get("document", "").strip()

    if not query:
        return JsonResponse({"error": "Missing 'q' parameter"}, status=400)

    if document:
        results = search_by_document(document, query, top_k=top_k)
    else:
        results = search_similar(query, top_k=top_k)

    return JsonResponse({"query": query, "results": results})


# ── ViewerJS PDF display ─────────────────────────────────────────────────

def viewer_view(request, document_id):
    """
    Display a PDF via ViewerJS at a specific page.
    URL: /librarian/viewer/<document_id>/?page=3
    """
    try:
        doc = ArchiveDocument.objects.get(pk=document_id)
    except ArchiveDocument.DoesNotExist:
        raise Http404("Document not found")

    page = int(request.GET.get("page", 1))

    # Build the URL to serve the PDF via our own view
    from django.urls import reverse
    pdf_url = reverse("the_librarian:serve_pdf", args=[document_id])

    return render(request, "the_librarian/viewer.html", {
        "document": doc,
        "page": page,
        "pdf_url": pdf_url,
    })


def serve_pdf(request, document_id):
    """
    Serve a PDF file from the archive directory.
    """
    from django.http import FileResponse

    try:
        doc = ArchiveDocument.objects.get(pk=document_id)
    except ArchiveDocument.DoesNotExist:
        raise Http404("Document not found")

    pdf_path = Path(settings.ARCHIVE_DIR) / doc.file_path
    if not pdf_path.exists():
        raise Http404("PDF file not found on disk")

    return FileResponse(
        open(pdf_path, "rb"),
        content_type="application/pdf",
        filename=doc.filename,
    )


# ── Admin ingestion trigger ──────────────────────────────────────────────

@staff_member_required
@require_POST
def trigger_ingestion(request):
    """
    Admin-only endpoint to trigger archive ingestion.
    Called via the dashboard button.
    """
    from the_librarian.services.ingestion import ingest_archive, clear_stop_signal
    
    # Clear stop signal before starting
    if not request.POST.get("filename"):
        clear_stop_signal()

    force = request.POST.get("force", "false").lower() == "true"
    filename = request.POST.get("filename")
    
    try:
        results = ingest_archive(force=force, filename=filename)
        return JsonResponse({
            "success": True,
            "processed": len(results["processed"]),
            "skipped": len(results["skipped"]),
            "errors": len(results["errors"]),
            "details": results,
        })
    except Exception as e:
        logger.exception("Ingestion failed")
        return JsonResponse({
            "success": False,
            "error": str(e),
        }, status=500)

@staff_member_required
@require_POST
def stop_ingestion(request):
    """Admin-only endpoint to request a stop to ingestion."""
    from the_librarian.services.ingestion import request_stop
    request_stop()
    return JsonResponse({"success": True, "message": "Stop requested"})

@staff_member_required
@require_GET
def get_pending_pdfs_view(request):
    """Admin-only endpoint to get list of pending PDFs."""
    from the_librarian.services.ingestion import get_pending_pdfs
    force = request.GET.get("force", "false").lower() == "true"
    pending = get_pending_pdfs(force=force)
    return JsonResponse({"success": True, "pending": pending})
