/* ───────────────────────────────────────────────────────────────────────────
   The glossary card.

   A word an editor marked as a glossary term arrives as

       <a class="glossary-term" href="/glossary/…/" data-glossary-id="12">

   and this script explains it in a small card:

     mouse      on hover; the card stays while the pointer is on it
     keyboard   when the word gets focus; Esc closes
     touch      on tap — a sheet at the foot of a narrow screen. A second tap
                on the word, or a tap anywhere else, closes it.

   Without this script the word is still an ordinary link to the term's page.

   The explanations for every term on the page come in one request, made when
   the page is idle. The card uses the language of the text the word is in
   (an article can have four); a term not explained in that language is shown
   in the first one it has. When a term is explained in several languages the
   card offers them, and keeps to the reader's pick until they leave the page.

   Loaded by base.html on every page, in both themes. It does nothing on a
   page with no marked words. No dependencies.
─────────────────────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  var SELECTOR = 'a.glossary-term[data-glossary-id]';
  var script = document.currentScript;
  var termsUrl = script && script.getAttribute('data-terms-url');
  if (!termsUrl || !window.fetch || !document.querySelector(SELECTOR)) return;

  // In the order a missing language falls back through.
  var LANGS = [['ml', 'മലയാളം'], ['en', 'English'], ['hi', 'हिन्दी'], ['ta', 'தமிழ்']];
  var SHOW_DELAY = 150;  // ms the pointer rests on a word before the card opens
  var HIDE_DELAY = 250;  // ms allowed to move from the word onto the card
  var BATCH = 100;       // terms asked for per request
  var GAP = 8;           // px between word and card, and card and window edge
  var SHEET = '(max-width: 600px)';

  var terms = {};        // id → {id, url, names: {lang: …}, text: {lang: html}}
  var loading = null;    // the one Promise for this page's terms
  var card = null;       // built the first time it is needed
  var parts = {};
  var current = null;    // the word the card is open for
  var wanted = null;     // the word the pointer or focus is on
  var point = null;      // where the pointer was, to pick a wrapped word's line
  var pickedLang = null; // a language the reader chose in the card
  var byTouch = false;   // the card was opened by a tap
  var lastPointer = 'mouse';
  var showTimer, hideTimer;

  function label(name, fallback) {
    return script.getAttribute('data-' + name + '-label') || fallback;
  }

  function wordAt(target) {
    return target && target.closest ? target.closest(SELECTOR) : null;
  }

  function termOf(el) {
    return terms[el.getAttribute('data-glossary-id')];
  }

  // ── Data ───────────────────────────────────────────────────────────────

  function load() {
    if (loading) return loading;
    var ids = [];
    document.querySelectorAll(SELECTOR).forEach(function (el) {
      var id = el.getAttribute('data-glossary-id');
      if (ids.indexOf(id) === -1) ids.push(id);
    });
    var requests = [];
    for (var i = 0; i < ids.length; i += BATCH) {
      requests.push(
        fetch(termsUrl + '?ids=' + ids.slice(i, i + BATCH).join(','), { credentials: 'same-origin' })
          .then(function (response) { return response.ok ? response.json() : {}; })
          .then(function (data) {
            Object.keys(data.terms || {}).forEach(function (id) { terms[id] = data.terms[id]; });
          })
          .catch(function () { /* the words stay plain links */ })
      );
    }
    loading = Promise.all(requests);
    return loading;
  }

  // ── Language ───────────────────────────────────────────────────────────

  // The language of the text a word sits in. Classic templates wrap each
  // body in [data-lang]; the print theme lists every language a block is
  // shown for in [data-pt-lang], its own first. Elsewhere: the site's.
  function textLanguage(el) {
    var holder = el.closest('[data-lang], [data-pt-lang]');
    var value = holder
      ? holder.getAttribute('data-lang') || holder.getAttribute('data-pt-lang') || ''
      : '';
    value = value.split(' ')[0] || document.documentElement.lang || 'ml';
    return value.toLowerCase().split('-')[0];
  }

  function explainedIn(term) {
    return LANGS.map(function (lang) { return lang[0]; })
      .filter(function (code) { return term.text && term.text[code]; });
  }

  function languageFor(term, el) {
    var has = explainedIn(term);
    if (pickedLang && has.indexOf(pickedLang) !== -1) return pickedLang;
    var own = textLanguage(el);
    return has.indexOf(own) !== -1 ? own : (has[0] || own);
  }

  // ── The card ───────────────────────────────────────────────────────────

  function build() {
    card = document.createElement('div');
    card.className = 'glossary-card';
    card.id = 'glossary-card';
    card.hidden = true;
    card.setAttribute('role', 'group');
    card.setAttribute('aria-label', label('card', 'Glossary'));
    card.innerHTML =
      '<div class="glossary-card__head">' +
        '<p class="glossary-card__names">' +
          '<strong class="glossary-card__name"></strong> ' +
          '<span class="glossary-card__other"></span>' +
        '</p>' +
        '<button type="button" class="glossary-card__close">&times;</button>' +
      '</div>' +
      '<div class="glossary-card__langs" role="group"></div>' +
      '<div class="glossary-card__text" id="glossary-card-text"></div>' +
      '<a class="glossary-card__more"></a>';
    ['name', 'other', 'close', 'langs', 'text', 'more'].forEach(function (name) {
      parts[name] = card.querySelector('.glossary-card__' + name);
    });
    parts.close.setAttribute('aria-label', label('close', 'Close'));
    parts.more.textContent = label('more', 'More');

    card.addEventListener('click', function (e) {
      var choice = e.target.closest('[data-glossary-lang]');
      if (choice) {
        pickedLang = choice.getAttribute('data-glossary-lang');
        render();
        place();
      } else if (e.target.closest('.glossary-card__close')) {
        hide();
      }
    });
    document.body.appendChild(card);
  }

  function render() {
    var term = termOf(current);
    var names = term.names || {};
    var lang = languageFor(term, current);
    var has = explainedIn(term);

    var name = names[lang] || names.ml || current.textContent;
    var other = lang === 'ml' ? names.en : names.ml;
    parts.name.textContent = name;
    parts.other.textContent = other && other !== name ? other : '';

    // Editors' rich text from this site, as trusted as the article around it.
    parts.text.innerHTML = (term.text && term.text[lang]) || '';
    parts.text.lang = lang;
    parts.more.href = term.url;

    parts.langs.textContent = '';
    parts.langs.hidden = has.length < 2;
    if (has.length < 2) return;
    LANGS.forEach(function (entry) {
      if (has.indexOf(entry[0]) === -1) return;
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'glossary-card__lang';
      button.textContent = entry[1];
      button.lang = entry[0];
      button.setAttribute('data-glossary-lang', entry[0]);
      button.setAttribute('aria-pressed', String(entry[0] === lang));
      parts.langs.appendChild(button);
    });
  }

  function place() {
    // A sheet only for fingers: a mouse could never reach a card down there
    // before it closed.
    var sheet = byTouch && window.matchMedia(SHEET).matches;
    card.classList.toggle('glossary-card--touch', byTouch);
    card.classList.toggle('glossary-card--sheet', sheet);
    if (sheet) {
      card.style.left = card.style.top = '';
      return;
    }
    // A marked phrase can wrap: use the line the pointer is on.
    var rects = current.getClientRects();
    var rect = rects[0] || current.getBoundingClientRect();
    if (point) {
      for (var i = 0; i < rects.length; i++) {
        if (point.x >= rects[i].left && point.x <= rects[i].right &&
            point.y >= rects[i].top && point.y <= rects[i].bottom) {
          rect = rects[i];
        }
      }
    }
    var width = card.offsetWidth;
    var height = card.offsetHeight;
    var left = rect.left + rect.width / 2 - width / 2;
    left = Math.max(GAP, Math.min(left, document.documentElement.clientWidth - width - GAP));
    var below = rect.bottom + GAP;
    var above = rect.top - GAP - height;
    var top = below + height > window.innerHeight && above >= 0 ? above : below;
    card.style.left = Math.round(left + window.scrollX) + 'px';
    card.style.top = Math.round(top + window.scrollY) + 'px';
  }

  function show(el) {
    if (!termOf(el)) return false;
    if (!card) build();
    if (current && current !== el) release();
    current = el;
    render();
    card.hidden = false;
    place();
    el.classList.add('is-open');
    el.setAttribute('aria-describedby', 'glossary-card-text');
    return true;
  }

  function release() {
    current.classList.remove('is-open');
    current.removeAttribute('aria-describedby');
    current = null;
  }

  function hide() {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    if (!current) return;
    release();
    card.hidden = true;
  }

  // ── Mouse and pen ──────────────────────────────────────────────────────

  document.addEventListener('pointerover', function (e) {
    if (e.pointerType === 'touch') return;
    var el = wordAt(e.target);
    if (el) {
      clearTimeout(hideTimer);
      clearTimeout(showTimer);
      wanted = el;
      if (el === current) return;
      point = { x: e.clientX, y: e.clientY };
      showTimer = setTimeout(function () {
        load().then(function () {
          if (wanted !== el) return;
          byTouch = false;
          show(el);
        });
      }, SHOW_DELAY);
    } else if (card && card.contains(e.target)) {
      clearTimeout(hideTimer);
    }
  });

  document.addEventListener('pointerout', function (e) {
    if (e.pointerType === 'touch') return;
    var from = wordAt(e.target) || (card && card.contains(e.target) ? card : null);
    if (!from) return;
    var to = e.relatedTarget;
    if (to && (from.contains(to) || (card && card.contains(to)))) return;
    wanted = null;
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, HIDE_DELAY);
  });

  // ── Keyboard ───────────────────────────────────────────────────────────

  document.addEventListener('focusin', function (e) {
    var el = wordAt(e.target);
    if (!el) return;
    var byKeyboard = lastPointer === 'keyboard';
    try { byKeyboard = el.matches(':focus-visible'); } catch (err) { /* old browser */ }
    if (!byKeyboard) return;
    wanted = el;
    point = null;
    load().then(function () {
      if (wanted !== el || document.activeElement !== el) return;
      byTouch = false;
      show(el);
    });
  });

  document.addEventListener('focusout', function (e) {
    var el = wordAt(e.target);
    if (!el) return;
    if (wanted === el) wanted = null;
    if (el === current && !(card && card.contains(e.relatedTarget))) hide();
  });

  document.addEventListener('keydown', function (e) {
    lastPointer = 'keyboard';
    if (e.key === 'Escape') {
      wanted = null;
      hide();
    }
  });

  // ── Touch, and closing ─────────────────────────────────────────────────

  document.addEventListener('pointerdown', function (e) {
    lastPointer = e.pointerType || 'mouse';
  }, true);

  document.addEventListener('click', function (e) {
    var el = wordAt(e.target);
    if (!el) {
      // By the path the click took: a language button is replaced as soon as
      // it is clicked, and is no longer inside the card by the time it gets here.
      var path = e.composedPath ? e.composedPath() : [];
      var inCard = card && (path.indexOf(card) !== -1 || card.contains(e.target));
      if (current && !inCard) hide();
      return;
    }
    // With a mouse or the keyboard the word is a link like any other.
    if ((e.pointerType || lastPointer) !== 'touch') return;
    e.preventDefault();
    if (el === current) {
      hide();
      return;
    }
    wanted = el;
    point = null;
    load().then(function () {
      if (wanted !== el) return;
      byTouch = true;
      // Nothing to show (the request failed): go where the link goes.
      if (!show(el)) window.location.assign(el.href);
    });
  });

  window.addEventListener('resize', function () {
    if (current) place();
  });

  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(load, { timeout: 3000 });
  } else {
    setTimeout(load, 1200);
  }
})();
