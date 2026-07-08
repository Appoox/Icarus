"""
the_librarian/services/text_cleanup.py
───────────────────────────────────────
Pure-Python Malayalam OCR text cleanup + chunking. Deliberately has ZERO
Django imports so it can be shared between the Django app (services/ingestion.py)
and the standalone remote worker (worker/ingest_worker.py) without either one
needing the other's dependencies.
"""

import re
import regex
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

NOISE_SCRIPT_PATTERN = regex.compile(
    r'[^\p{Script=Malayalam}\p{Script=Latin}\p{Script=Common}\p{Script=Inherited}\s]+'
)

AD_SIGNALS = [
    r'\+91[\s\-]?\d{5}[\s\-]?\d{5}',
    r'CNRB\s*\d+',
    r'IFS\s*[Cc]ode',
    r'sasthragath[y]?@gmail',
    r'ksspmagazine@gmail',
    r'Price\s+Rs',
    r'Registered\.\s*No',
    r'www\.[a-z]+\.(com|in)',
    r'വരിസംഖ്യ.*രൂപ',
    r'ബാങ്കിൽ\s+പണ',
]


def _is_metadata_chunk(text: str) -> bool:
    hits = sum(1 for p in AD_SIGNALS if re.search(p, text))
    return hits >= 2


def _remove_noise_chars(text: str) -> str:
    return NOISE_SCRIPT_PATTERN.sub('', text)


def _fix_broken_malayalam_words(text: str) -> str:
    text = re.sub(r'([ഀ-ൿa-zA-Z])\s*[-—]\s*\n\s*([ഀ-ൿa-zA-Z])', r'\1\2', text)
    text = re.sub(r'([ഀ-ൿ])\s*\n\s*([ഀ-ൿ])', r'\1 \2', text)
    text = re.sub(r'([ഀ-ൿ])\s*\n\s*[—\-]\s*\n\s*([ഀ-ൿ])', r'\1 \2', text)
    text = re.sub(r'([ഀ-ൿ])\u00ad([ഀ-ൿ])', r'\1\2', text)
    return text


def _remove_noise_lines(text: str) -> str:
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append('')
            continue
        has_malayalam = bool(re.search(r'[ഀ-ൿ]', stripped))
        has_latin_word = bool(re.search(r'[a-zA-Z]{3,}', stripped))
        if not has_malayalam and not has_latin_word:
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)


def _dedupe_repeated_lines(text: str) -> str:
    lines = text.split('\n')
    seen = set()
    unique = []
    for line in lines:
        key = re.sub(r'\s+', '', line).lower()
        if len(key) < 30:
            unique.append(line)
            continue
        if key not in seen:
            seen.add(key)
            unique.append(line)
    return '\n'.join(unique)


def _dedupe_repeated_paragraphs(text: str) -> str:
    paragraphs = re.split(r'\n{2,}', text)
    seen = set()
    unique = []
    for para in paragraphs:
        key = re.sub(r'\s+', '', para).lower()
        if len(key) < 20:
            unique.append(para)
            continue
        if key not in seen:
            seen.add(key)
            unique.append(para)
    return '\n\n'.join(unique)


def _remove_hallucinated_loops(text: str) -> str:
    word_pattern = r'(?P<phrase>(?:\S+\s+){0,9}\S+)(?:\s+(?P=phrase)){1,}'
    for _ in range(5):
        prev_text = text
        text = re.sub(word_pattern, r'\g<phrase>', text, flags=re.IGNORECASE)
        if text == prev_text:
            break
    char_pattern = r'(?P<syl>[a-zA-Zഀ-ൿ]{1,15}?)(?P=syl){3,}'
    text = re.sub(char_pattern, r'\g<syl>', text, flags=re.IGNORECASE)
    return text


def _remove_page_headers_footers(text: str) -> str:
    text = re.sub(r'\d*\s*ശാസ്ത്രഗതി\s*[\|]?\s*(?:ആഗസ്റ്റ്|ത്രഗസ്റ്റ്)\s*\d{4}', '', text)
    text = re.sub(r'(?:ആഗസ്റ്റ്|ത്രഗസ്റ്റ്)\s*\d{4}\s*[\|]?\s*ശാസ്ത്രഗതി\s*\d*', '', text)
    text = re.sub(
        r'SASTHRAGATHI\s*\n.*?AUGUST\s*\d{4}.*?\n.*?Price.*?\n',
        '', text, flags=re.DOTALL
    )
    return text


def preprocess_malayalam_pdf_text(text: str, chunk_id: int = None) -> str | None:
    if not isinstance(text, str):
        return None
    if _is_metadata_chunk(text):
        return None
    text = re.sub(r'\n', '', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\\overline\{[^\}]+\}', '', text)
    text = _remove_page_headers_footers(text)
    text = _fix_broken_malayalam_words(text)
    text = _remove_noise_chars(text)
    text = _remove_hallucinated_loops(text)
    text = re.sub(r'[-_.=~—]{3,}', ' ', text)
    text = _remove_noise_lines(text)
    text = _dedupe_repeated_lines(text)
    text = re.sub(r'[_—\-]*<[^>]+>[_—\-]*', ' ', text)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    malayalam_chars = len(re.findall(r'[ഀ-ൿ]', text))
    if len(text) < 80 or malayalam_chars < 20:
        return None
    return text


def preprocess_chunks(rows, text_field="text", log_discarded=True):
    cleaned = []
    discarded = 0
    for row in rows:
        original_text = row.get(text_field, "")
        result = preprocess_malayalam_pdf_text(original_text, chunk_id=row.get("id"))
        if result is None:
            discarded += 1
            if log_discarded:
                preview = original_text[:60].replace('\n', ' ')
                print(f"[DISCARDED] id={row.get('id')} | {preview!r}")
            continue
        cleaned.append({**row, text_field: result})
    print(f"\nDone: {len(cleaned)} kept, {discarded} discarded out of {len(rows)} total")
    return cleaned


def chunk_pages(page_texts):
    """
    page_texts: list of (page_number, raw_text) tuples.
    Returns list of dicts: {chunk_text, page_number, chunk_index}.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        separators=["\n\n", "\n", " ", ""],
        is_separator_regex=False,
    )

    all_chunks = []
    global_index = 0

    for page_number, text in page_texts:
        if not text or not text.strip():
            continue

        cleaned_text = preprocess_malayalam_pdf_text(text)
        if cleaned_text is None:
            continue

        doc = Document(page_content=cleaned_text, metadata={"page_number": page_number})

        try:
            chunks = splitter.split_documents([doc])
        except Exception:
            chunks = [doc]

        for chunk in chunks:
            chunk_text = chunk.page_content.strip()
            malayalam_chars = len(re.findall(r'[ഀ-ൿ]', chunk_text))
            if len(chunk_text) < 40 or malayalam_chars < 30:
                continue
            all_chunks.append({
                "chunk_text": chunk_text,
                "page_number": page_number,
                "chunk_index": global_index,
            })
            global_index += 1

    return all_chunks