import logging
import re
from django.conf import settings
from django.db import transaction
from the_librarian.models import DocumentChunk
from the_librarian.services.embedder import embed_texts
from langchain_text_splitters import RecursiveCharacterTextSplitter

from the_librarian.services.ingestion import preprocess_malayalam_pdf_text

logger = logging.getLogger(__name__)

def get_text_from_streamfield(stream_value):
    """Extract plain text from a Wagtail StreamField value."""
    if not stream_value:
        return ""
    texts = []
    for block in stream_value:
        if block.block_type in ['paragraph', 'text', 'rich_text', 'heading', 'colored_heading', 'blockquote']:
            val = block.value
            if isinstance(val, dict):
                text_val = val.get('text', val.get('paragraph', ''))
                if text_val:
                    val = text_val
            if hasattr(val, 'source'):
                clean_txt = re.sub(r'<[^>]+>', ' ', val.source)
                texts.append(clean_txt)
            else:
                texts.append(str(val))
    return "\n".join(texts)

def index_article(article):
    """
    Index an Article page: title + body for all available languages.
    """
    from articles.models import Article
    if isinstance(article, (int, str)):
        article = Article.objects.get(pk=article)

    DocumentChunk.objects.filter(article=article).delete()

    lang_fields = {
        'ml': ('title', 'body'),
        'en': ('title_en', 'body_en'),
        'hi': ('title_hi', 'body_hi'),
        'ta': ('title_ta', 'body_ta'),
    }

    all_chunks_to_create = []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        separators=["\n\n", "\n", " ", ""]
    )

    for lang, (t_attr, b_attr) in lang_fields.items():
        title = getattr(article, t_attr, "")
        body_stream = getattr(article, b_attr, None)
        body_text = get_text_from_streamfield(body_stream)
        full_text = f"{title}\n\n{body_text}".strip()

        if not full_text or len(full_text) < 50:
            continue

        if lang == 'ml':
            cleaned = preprocess_malayalam_pdf_text(full_text)
            if cleaned:
                full_text = cleaned

        chunks = splitter.split_text(full_text)
        if not chunks:
            continue

        embeddings = embed_texts(chunks)

        for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            all_chunks_to_create.append(DocumentChunk(
                article=article,
                chunk_text=chunk_text,
                embedding=embedding,
                language=lang,
                chunk_index=i
            ))

    if all_chunks_to_create:
        with transaction.atomic():
            DocumentChunk.objects.bulk_create(all_chunks_to_create)

    logger.info(f"Indexed article {article.id}: {len(all_chunks_to_create)} chunks created.")
    return len(all_chunks_to_create)

def index_author(author):
    """
    Index a Literati (Author) profile.
    """
    from literati.models import Literati
    if isinstance(author, (int, str)):
        author = Literati.objects.get(pk=author)

    DocumentChunk.objects.filter(author=author).delete()

    name = author.title
    role = getattr(author, 'role', '')
    bio = getattr(author, 'bio', '')

    if hasattr(bio, 'source'):
        bio = re.sub(r'<[^>]+>', ' ', bio.source)
    else:
        bio = str(bio)

    full_text = f"{name}\n{role}\n\n{bio}".strip()

    if not full_text or len(full_text) < 20:
        return 0

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        separators=["\n\n", "\n", " ", ""]
    )

    chunks = splitter.split_text(full_text)
    if not chunks:
        return 0

    embeddings = embed_texts(chunks)

    chunks_to_create = [
        DocumentChunk(
            author=author,
            chunk_text=chunk_text,
            embedding=embedding,
            language='ml',
            chunk_index=i
        )
        for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings))
    ]

    if chunks_to_create:
        with transaction.atomic():
            DocumentChunk.objects.bulk_create(chunks_to_create)

    logger.info(f"Indexed author {author.id}: {len(chunks_to_create)} chunks created.")
    return len(chunks_to_create)


def index_editorial(issue):
    """
    Index the editorial StreamField of an Issue page.

    Extracts text from the Issue's `editorial` StreamField, chunks and embeds
    it, then stores DocumentChunk rows with the `issue` FK set.  Old chunks
    for this issue are deleted first to allow clean re-indexing.

    Args:
        issue: Issue instance or PK (int/str).

    Returns:
        int — number of chunks created.
    """
    from issue.models import Issue
    if isinstance(issue, (int, str)):
        issue = Issue.objects.get(pk=issue)

    # Clear any existing editorial chunks for this issue
    DocumentChunk.objects.filter(issue=issue).delete()

    # Extract text from the editorial StreamField
    editorial_stream = getattr(issue, 'editorial', None)
    editorial_text = get_text_from_streamfield(editorial_stream)

    # Combine the issue title with the editorial body for richer context
    full_text = f"{issue.title}\n\n{editorial_text}".strip()

    if not full_text or len(full_text) < 50:
        logger.info(
            "Skipping editorial indexing for Issue %s — insufficient text (%d chars).",
            issue.id, len(full_text),
        )
        return 0

    # Optional: apply Malayalam cleaning if the editorial is in Malayalam
    cleaned = preprocess_malayalam_pdf_text(full_text)
    if cleaned:
        full_text = cleaned

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        separators=["\n\n", "\n", " ", ""]
    )

    chunks = splitter.split_text(full_text)
    if not chunks:
        return 0

    embeddings = embed_texts(chunks)

    chunks_to_create = [
        DocumentChunk(
            issue=issue,
            chunk_text=chunk_text,
            embedding=embedding,
            language='ml',   # Issue editorials are Malayalam-primary
            chunk_index=i,
        )
        for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings))
    ]

    if chunks_to_create:
        with transaction.atomic():
            DocumentChunk.objects.bulk_create(chunks_to_create)

    logger.info(
        "Indexed editorial for Issue %s ('%s'): %d chunks created.",
        issue.id, issue.title, len(chunks_to_create),
    )
    return len(chunks_to_create)