"""
The reader's side of the glossary.

    /glossary/                    every term, under its first letter
    /glossary/<slug>/             one term in every language it has, and the
                                  articles that use it
    /glossary/terms.json?ids=…    what the card shows for the terms marked on
                                  a page (fetched once by static/js/glossary.js)
"""
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.cache import patch_cache_control
from django.utils.translation import get_language
from django.views.decorators.http import require_GET

from home.theme import themed_template

from .models import TERM_LANGUAGES, GlossaryTerm

#: Most terms one request may ask for (the script asks in batches of 100).
MAX_TERMS_PER_REQUEST = 200

#: The articles listed on a term's page.
MAX_ARTICLES = 30


def _site_language():
    """'en' on the English site (/en/…), 'ml' everywhere else."""
    return 'en' if (get_language() or '').startswith('en') else 'ml'


def _other(lang):
    return 'ml' if lang == 'en' else 'en'


def index(request):
    lang = _site_language()
    entries = []
    for term in GlossaryTerm.objects.all():
        names = term.names
        name = names.get(lang) or term.term
        other = names.get(_other(lang), '')
        text_lang, text = term.explanation_for(lang)
        entries.append({
            'term': term,
            'name': name,
            'other_name': other if other != name else '',
            'text': text,
            'text_lang': text_lang,
        })
    entries.sort(key=lambda entry: entry['name'].casefold())

    groups = []   # [(first letter, [entry, …]), …] in alphabetical order
    for entry in entries:
        letter = entry['name'][:1].upper()
        if not groups or groups[-1][0] != letter:
            groups.append((letter, []))
        groups[-1][1].append(entry)

    return render(request, themed_template(request, 'glossary/index.html'), {
        'groups': groups,
        'count': len(entries),
    })


def term_detail(request, slug):
    term = get_object_or_404(GlossaryTerm, slug=slug)
    lang = _site_language()
    names, texts = term.names, term.explanations

    # The site's language first, then the rest in their usual order.
    order = [lang] + [code for code, _own in TERM_LANGUAGES if code != lang]
    own_names = dict(TERM_LANGUAGES)
    languages = [
        {
            'code': code,
            'label': own_names[code],
            'name': names.get(code, ''),
            'text': texts.get(code, ''),
        }
        for code in order if code in names or code in texts
    ]
    name = names.get(lang) or term.term
    other = names.get(_other(lang), '')
    return render(request, themed_template(request, 'glossary/term.html'), {
        'term': term,
        'name': name,
        'other_name': other if other != name else '',
        'languages': languages,
        'articles': term.articles()[:MAX_ARTICLES],
    })


@require_GET
def terms_json(request):
    ids = [
        int(part) for part in request.GET.get('ids', '').split(',')
        if part.isascii() and part.isdigit() and len(part) < 10
    ][:MAX_TERMS_PER_REQUEST]
    terms = GlossaryTerm.objects.filter(pk__in=ids) if ids else []
    response = JsonResponse({'terms': {str(t.pk): t.as_card() for t in terms}})
    # Public, and the same for every reader: let browsers keep it a while.
    patch_cache_control(response, public=True, max_age=300)
    return response
