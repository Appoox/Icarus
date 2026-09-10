/*
 * homepage_layout.js — the Homepage Layout canvas.
 *
 * Loaded only by wagtailadmin/homepage_layout.html, never globally.
 *
 * Three things happen here:
 *
 *   1. Placement.  A 12-column grid (6 on tablet, 1 on mobile) that you drag
 *      section cards around on and resize by their right and bottom edges.
 *      Everything snaps to grid cells; nothing is stored in pixels.
 *
 *   2. Content.  The inspector is rendered from a schema the server derives
 *      from the block definitions (see field_schema in layout_views.py), so
 *      adding a section type in home/blocks.py makes its settings editable
 *      here with no change to this file.  Fields the inspector cannot render
 *      safely arrive with kind 'external' and get a deep-link into the normal
 *      page editor instead of a control that would mangle them.
 *
 *   3. Saving.  One JSON POST carrying both the sections and the placement.
 *      The server validates it through Wagtail's own blocks and writes a page
 *      revision, so drafts, publish, history and rollback behave exactly as
 *      they do for any other page.
 *
 * Row heights are deliberately a guide, not a measurement: the real height of
 * a section comes from its content at render time.  The canvas draws a
 * proportional placeholder and the Preview button shows the truth.
 */
(function () {
    'use strict';

    const root = document.querySelector('[data-sg-canvas]');
    const dataEl = document.getElementById('sg-canvas-data');
    if (!root || !dataEl) return;

    let DATA;
    try {
        DATA = JSON.parse(dataEl.textContent);
    } catch (e) {
        return;
    }

    const gridEl = root.querySelector('[data-sg-grid]');
    const inspectorEl = root.querySelector('[data-sg-inspector]');
    const statusEl = root.querySelector('[data-sg-status]');
    const addToggle = root.querySelector('[data-sg-add-toggle]');
    const addMenu = root.querySelector('[data-sg-add-menu]');
    const previewPanel = root.querySelector('[data-sg-preview-panel]');
    const previewFrame = root.querySelector('[data-sg-preview-frame]');

    const ROW_PX = 56;      // one grid row on the canvas
    const GUTTER = 8;

    const state = {
        cards: DATA.cards.slice(),
        layout: DATA.layout,
        breakpoint: 'desktop',
        selected: null,
        dirty: false,
    };

    // ── Helpers ─────────────────────────────────────────────────────────

    const columns = () => DATA.columns[state.breakpoint] || 12;

    function uuid() {
        if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            const r = (Math.random() * 16) | 0;
            return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
        });
    }

    function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

    function placement(id) {
        const map = state.layout[state.breakpoint] || (state.layout[state.breakpoint] = {});
        if (!map[id]) {
            // Falling back to the desktop placement is what makes the tablet
            // and mobile tabs start out "inherited" rather than empty: you
            // see the desktop arrangement and only diverge where you drag.
            const base = (state.layout.desktop || {})[id] || {};
            const cols = columns();
            map[id] = {
                col: 1,
                span: clamp(base.span || cols, 1, cols),
                row: base.row || 1,
                row_span: base.row_span || 1,
                visible: base.visible !== false,
                style: base.style || 'plain',
                pad: base.pad || 'md',
            };
        }
        return map[id];
    }

    function setStatus(message, tone) {
        statusEl.textContent = message || '';
        statusEl.className = 'sg-canvas__status' + (tone ? ' is-' + tone : '');
    }

    function markDirty() {
        state.dirty = true;
        setStatus('Unsaved changes.', 'warn');
    }

    function csrfToken() {
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    // ── Grid rendering ──────────────────────────────────────────────────

    function renderGrid() {
        gridEl.innerHTML = '';
        gridEl.style.setProperty('--sg-canvas-cols', columns());

        const ordered = state.cards.slice().sort(function (a, b) {
            const pa = placement(a.id), pb = placement(b.id);
            return (pa.row - pb.row) || (pa.col - pb.col);
        });

        let maxRow = 1;
        ordered.forEach(function (card) {
            const p = placement(card.id);
            maxRow = Math.max(maxRow, p.row + p.row_span - 1);
            gridEl.appendChild(buildCard(card, p));
        });

        // Two spare rows so there is always somewhere to drag a section to.
        gridEl.style.setProperty('--sg-canvas-rows', maxRow + 2);
        gridEl.style.setProperty('--sg-row-px', ROW_PX + 'px');
    }

    function buildCard(card, p) {
        const el = document.createElement('div');
        el.className = 'sg-card' + (p.visible ? '' : ' is-hidden')
            + (state.selected === card.id ? ' is-selected' : '');
        el.dataset.id = card.id;
        el.style.gridColumn = p.col + ' / span ' + p.span;
        el.style.gridRow = p.row + ' / span ' + p.row_span;
        el.tabIndex = 0;
        el.setAttribute('role', 'button');
        el.setAttribute('aria-label', card.label + (p.visible ? '' : ' (hidden)'));

        const head = document.createElement('div');
        head.className = 'sg-card__head';
        head.innerHTML = '<span class="sg-card__label"></span>'
            + '<span class="sg-card__size"></span>';
        head.querySelector('.sg-card__label').textContent = card.label;
        head.querySelector('.sg-card__size').textContent = p.span + '×' + p.row_span;
        el.appendChild(head);

        if (card.summary) {
            const sum = document.createElement('p');
            sum.className = 'sg-card__summary';
            sum.textContent = card.summary;
            el.appendChild(sum);
        }

        ['e', 's', 'se'].forEach(function (dir) {
            const handle = document.createElement('span');
            handle.className = 'sg-card__handle sg-card__handle--' + dir;
            handle.dataset.resize = dir;
            el.appendChild(handle);
        });

        el.addEventListener('pointerdown', onPointerDown);
        el.addEventListener('keydown', onCardKeydown);
        return el;
    }

    // ── Drag and resize ─────────────────────────────────────────────────
    //
    // One pointer handler covers both: whether the gesture started on a
    // resize handle decides which numbers it edits.  Pointer capture keeps
    // the gesture alive when the cursor leaves the card, which matters when
    // you shrink a section faster than the layout reflows.

    let gesture = null;

    function cellSize() {
        const rect = gridEl.getBoundingClientRect();
        return {
            w: (rect.width - GUTTER * (columns() - 1)) / columns() + GUTTER,
            h: ROW_PX + GUTTER,
        };
    }

    function onPointerDown(event) {
        if (event.button !== 0) return;
        const el = event.currentTarget;
        const card = state.cards.find(function (c) { return c.id === el.dataset.id; });
        if (!card) return;

        select(card.id);

        const resize = event.target.dataset ? event.target.dataset.resize : null;
        const p = placement(card.id);
        gesture = {
            el: el,
            id: card.id,
            mode: resize || 'move',
            startX: event.clientX,
            startY: event.clientY,
            start: { col: p.col, row: p.row, span: p.span, row_span: p.row_span },
            cell: cellSize(),
            moved: false,
        };
        el.setPointerCapture(event.pointerId);
        el.classList.add('is-dragging');
        window.addEventListener('pointermove', onPointerMove);
        window.addEventListener('pointerup', onPointerUp);
        event.preventDefault();
    }

    function onPointerMove(event) {
        if (!gesture) return;
        const dx = Math.round((event.clientX - gesture.startX) / gesture.cell.w);
        const dy = Math.round((event.clientY - gesture.startY) / gesture.cell.h);
        if (dx === 0 && dy === 0 && !gesture.moved) return;
        gesture.moved = true;

        const p = placement(gesture.id);
        const cols = columns();
        const s = gesture.start;

        if (gesture.mode === 'move') {
            p.col = clamp(s.col + dx, 1, cols - p.span + 1);
            p.row = Math.max(1, s.row + dy);
        } else {
            if (gesture.mode.indexOf('e') !== -1) {
                p.span = clamp(s.span + dx, 1, cols - p.col + 1);
            }
            if (gesture.mode.indexOf('s') !== -1) {
                p.row_span = Math.max(1, s.row_span + dy);
            }
        }

        gesture.el.style.gridColumn = p.col + ' / span ' + p.span;
        gesture.el.style.gridRow = p.row + ' / span ' + p.row_span;
        const size = gesture.el.querySelector('.sg-card__size');
        if (size) size.textContent = p.span + '×' + p.row_span;
    }

    function onPointerUp() {
        if (!gesture) return;
        gesture.el.classList.remove('is-dragging');
        window.removeEventListener('pointermove', onPointerMove);
        window.removeEventListener('pointerup', onPointerUp);
        if (gesture.moved) {
            markDirty();
            renderGrid();
            renderInspector();
        }
        gesture = null;
    }

    // Keyboard equivalents — a drag-only canvas is unusable without them.
    function onCardKeydown(event) {
        const id = event.currentTarget.dataset.id;
        const p = placement(id);
        const cols = columns();
        const shift = event.shiftKey;
        let handled = true;

        switch (event.key) {
            case 'ArrowLeft':
                if (shift) p.span = clamp(p.span - 1, 1, cols - p.col + 1);
                else p.col = clamp(p.col - 1, 1, cols - p.span + 1);
                break;
            case 'ArrowRight':
                if (shift) p.span = clamp(p.span + 1, 1, cols - p.col + 1);
                else p.col = clamp(p.col + 1, 1, cols - p.span + 1);
                break;
            case 'ArrowUp':
                if (shift) p.row_span = Math.max(1, p.row_span - 1);
                else p.row = Math.max(1, p.row - 1);
                break;
            case 'ArrowDown':
                if (shift) p.row_span = p.row_span + 1;
                else p.row = p.row + 1;
                break;
            case 'Enter':
            case ' ':
                select(id);
                handled = true;
                break;
            default:
                handled = false;
        }

        if (handled) {
            event.preventDefault();
            if (event.key !== 'Enter' && event.key !== ' ') {
                markDirty();
                renderGrid();
                const next = gridEl.querySelector('[data-id="' + id + '"]');
                if (next) next.focus();
            }
        }
    }

    function select(id) {
        state.selected = id;
        gridEl.querySelectorAll('.sg-card').forEach(function (el) {
            el.classList.toggle('is-selected', el.dataset.id === id);
        });
        renderInspector();
    }

    // ── Inspector ───────────────────────────────────────────────────────

    function renderInspector() {
        inspectorEl.innerHTML = '';
        const card = state.cards.find(function (c) { return c.id === state.selected; });
        if (!card) {
            const empty = document.createElement('p');
            empty.className = 'sg-inspector__empty';
            empty.textContent = 'Select a section to edit it.';
            inspectorEl.appendChild(empty);
            return;
        }

        const p = placement(card.id);

        const title = document.createElement('h2');
        title.className = 'sg-inspector__title';
        title.textContent = card.label;
        inspectorEl.appendChild(title);

        inspectorEl.appendChild(placementSection(card, p));

        if (card.schema && card.schema.kind === 'struct') {
            const content = document.createElement('div');
            content.className = 'sg-inspector__group';
            const h = document.createElement('h3');
            h.textContent = 'Content';
            content.appendChild(h);
            card.schema.fields.forEach(function (field) {
                content.appendChild(renderField(field, function () {
                    markDirty();
                    refreshSummary(card);
                }));
            });
            inspectorEl.appendChild(content);
        } else if (card.schema) {
            inspectorEl.appendChild(renderField(card.schema, function () {
                markDirty();
                refreshSummary(card);
            }));
        }

        const actions = document.createElement('div');
        actions.className = 'sg-inspector__actions';

        const editLink = document.createElement('a');
        editLink.className = 'button button-small button-secondary';
        editLink.href = DATA.urls.edit;
        editLink.target = '_blank';
        editLink.rel = 'noopener';
        editLink.textContent = 'Edit in page editor';
        actions.appendChild(editLink);

        const dup = document.createElement('button');
        dup.type = 'button';
        dup.className = 'button button-small button-secondary';
        dup.textContent = 'Duplicate';
        dup.addEventListener('click', function () { duplicateCard(card); });
        actions.appendChild(dup);

        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'button button-small no';
        del.textContent = 'Delete';
        del.addEventListener('click', function () { deleteCard(card); });
        actions.appendChild(del);

        inspectorEl.appendChild(actions);
    }

    function placementSection(card, p) {
        const wrap = document.createElement('div');
        wrap.className = 'sg-inspector__group';
        const h = document.createElement('h3');
        h.textContent = 'Placement — ' + state.breakpoint;
        wrap.appendChild(h);

        const cols = columns();
        [
            ['col', 'Column', 1, cols],
            ['span', 'Width', 1, cols],
            ['row', 'Row', 1, 200],
            ['row_span', 'Height', 1, 24],
        ].forEach(function (spec) {
            wrap.appendChild(numberRow(spec[1], p[spec[0]], spec[2], spec[3], function (v) {
                p[spec[0]] = v;
                if (spec[0] === 'span') p.col = clamp(p.col, 1, cols - p.span + 1);
                if (spec[0] === 'col') p.col = clamp(p.col, 1, cols - p.span + 1);
                markDirty();
                renderGrid();
            }));
        });

        wrap.appendChild(checkboxRow('Visible', p.visible, function (v) {
            p.visible = v;
            markDirty();
            renderGrid();
        }));

        wrap.appendChild(selectRow('Background', p.style,
            [['plain', 'Plain'], ['tint', 'Tinted'], ['bordered', 'Bordered']],
            function (v) { p.style = v; markDirty(); }));

        wrap.appendChild(selectRow('Padding', p.pad,
            [['none', 'None'], ['sm', 'Small'], ['md', 'Medium'], ['lg', 'Large']],
            function (v) { p.pad = v; markDirty(); }));

        if (state.breakpoint !== 'desktop') {
            const reset = document.createElement('button');
            reset.type = 'button';
            reset.className = 'button button-small button-secondary';
            reset.textContent = 'Reset to desktop';
            reset.addEventListener('click', function () {
                delete state.layout[state.breakpoint][card.id];
                markDirty();
                renderGrid();
                renderInspector();
            });
            wrap.appendChild(reset);
        }

        return wrap;
    }

    // ── Field widgets ───────────────────────────────────────────────────

    function labelled(labelText, control, help) {
        const row = document.createElement('label');
        row.className = 'sg-field';
        const span = document.createElement('span');
        span.className = 'sg-field__label';
        span.textContent = labelText;
        row.appendChild(span);
        row.appendChild(control);
        if (help) {
            const hint = document.createElement('small');
            hint.className = 'sg-field__help';
            hint.textContent = help;
            row.appendChild(hint);
        }
        return row;
    }

    function numberRow(label, value, min, max, onChange) {
        const input = document.createElement('input');
        input.type = 'number';
        input.value = value;
        if (min !== null && min !== undefined) input.min = min;
        if (max !== null && max !== undefined) input.max = max;
        input.addEventListener('change', function () {
            onChange(clamp(parseInt(input.value, 10) || min || 0, min, max));
        });
        return labelled(label, input);
    }

    function checkboxRow(label, value, onChange) {
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = !!value;
        input.addEventListener('change', function () { onChange(input.checked); });
        const row = labelled(label, input);
        row.classList.add('sg-field--check');
        return row;
    }

    function selectRow(label, value, choices, onChange, help) {
        const select = document.createElement('select');
        choices.forEach(function (choice) {
            const opt = document.createElement('option');
            opt.value = choice[0];
            opt.textContent = choice[1];
            if (String(choice[0]) === String(value)) opt.selected = true;
            select.appendChild(opt);
        });
        select.addEventListener('change', function () { onChange(select.value); });
        return labelled(label, select, help);
    }

    /*
     * renderField is the generic half: it reads the schema the server built
     * from the block definitions and returns a control. `field.value` is
     * mutated in place, so saving just walks the schema back into JSON.
     */
    function renderField(field, onChange) {
        const commit = function (v) { field.value = v; onChange(); };

        switch (field.kind) {
            case 'text': {
                const input = document.createElement('input');
                input.type = 'text';
                input.value = field.value == null ? '' : field.value;
                input.addEventListener('input', function () { commit(input.value); });
                return labelled(field.label, input, field.help);
            }
            case 'textarea': {
                const area = document.createElement('textarea');
                area.rows = 3;
                area.value = field.value == null ? '' : field.value;
                area.addEventListener('input', function () { commit(area.value); });
                return labelled(field.label, area, field.help);
            }
            case 'number': {
                const input = document.createElement('input');
                input.type = 'number';
                input.value = field.value == null ? '' : field.value;
                if (field.min != null) input.min = field.min;
                if (field.max != null) input.max = field.max;
                input.addEventListener('change', function () {
                    commit(parseInt(input.value, 10));
                });
                return labelled(field.label, input, field.help);
            }
            case 'boolean': {
                const input = document.createElement('input');
                input.type = 'checkbox';
                input.checked = !!field.value;
                input.addEventListener('change', function () { commit(input.checked); });
                const row = labelled(field.label, input, field.help);
                row.classList.add('sg-field--check');
                return row;
            }
            case 'colour': {
                const input = document.createElement('input');
                input.type = 'color';
                input.value = field.value || '#c8102e';
                input.addEventListener('change', function () { commit(input.value); });
                return labelled(field.label, input, field.help);
            }
            case 'choice': {
                const choices = (field.choices || []).map(function (c) {
                    return [c.value, c.label];
                });
                return selectRow(field.label, field.value, choices, commit, field.help);
            }
            case 'page':
            case 'image':
            case 'snippet':
                return chooserField(field, commit);
            case 'list':
                return listField(field, onChange);
            case 'struct': {
                const group = document.createElement('div');
                group.className = 'sg-subgroup';
                const h = document.createElement('h4');
                h.textContent = field.label;
                group.appendChild(h);
                (field.fields || []).forEach(function (child) {
                    group.appendChild(renderField(child, onChange));
                });
                return group;
            }
            default: {
                // 'external' — rich text, embeds, raw HTML.  Be honest about
                // it rather than offering a control that would corrupt the
                // value on the way back out.
                const note = document.createElement('p');
                note.className = 'sg-field__external';
                const link = document.createElement('a');
                link.href = DATA.urls.edit;
                link.target = '_blank';
                link.rel = 'noopener';
                link.textContent = 'edit in the page editor';
                note.appendChild(document.createTextNode(field.label + ' — '));
                note.appendChild(link);
                return note;
            }
        }
    }

    /*
     * Chooser fields use a small type-ahead against our own JSON endpoint
     * rather than Wagtail's modal machinery.  That keeps the canvas
     * independent of admin internals, and picking an article without a modal
     * round-trip is the faster interaction anyway.
     */
    function chooserField(field, commit) {
        const wrap = document.createElement('div');
        wrap.className = 'sg-chooser';

        const label = document.createElement('span');
        label.className = 'sg-field__label';
        label.textContent = field.label;
        wrap.appendChild(label);

        const current = document.createElement('div');
        current.className = 'sg-chooser__current';
        const setCurrent = function (display) {
            current.textContent = display && display.title ? display.title : 'Nothing chosen';
            current.classList.toggle('is-empty', !(display && display.title));
        };
        setCurrent(field.display);
        wrap.appendChild(current);

        const search = document.createElement('input');
        search.type = 'search';
        search.placeholder = 'Search…';
        search.className = 'sg-chooser__search';
        wrap.appendChild(search);

        const results = document.createElement('ul');
        results.className = 'sg-chooser__results';
        wrap.appendChild(results);

        const clear = document.createElement('button');
        clear.type = 'button';
        clear.className = 'button button-small button-secondary';
        clear.textContent = 'Clear';
        clear.addEventListener('click', function () {
            commit(null);
            setCurrent(null);
            results.innerHTML = '';
        });
        wrap.appendChild(clear);

        let timer = null;
        const run = function () {
            const url = DATA.urls.search
                + '?kind=' + encodeURIComponent(field.target || field.kind)
                + '&q=' + encodeURIComponent(search.value);
            fetch(url, { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (payload) {
                    results.innerHTML = '';
                    (payload.results || []).forEach(function (item) {
                        const li = document.createElement('li');
                        const btn = document.createElement('button');
                        btn.type = 'button';
                        btn.textContent = item.title;
                        btn.addEventListener('click', function () {
                            commit(item.id);
                            setCurrent(item);
                            results.innerHTML = '';
                            search.value = '';
                        });
                        li.appendChild(btn);
                        results.appendChild(li);
                    });
                })
                .catch(function () { results.innerHTML = ''; });
        };

        search.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(run, 200);
        });
        search.addEventListener('focus', run);

        return wrap;
    }

    /* Repeatable rows — this is what makes hand-picking work: `picks` is a
     * ListBlock, so the editor adds, reorders and removes items here. */
    function listField(field, onChange) {
        const wrap = document.createElement('div');
        wrap.className = 'sg-list';

        const head = document.createElement('div');
        head.className = 'sg-list__head';
        const label = document.createElement('span');
        label.className = 'sg-field__label';
        label.textContent = field.label;
        head.appendChild(label);
        wrap.appendChild(head);

        const body = document.createElement('div');
        wrap.appendChild(body);

        const draw = function () {
            body.innerHTML = '';
            field.items.forEach(function (item, index) {
                const row = document.createElement('div');
                row.className = 'sg-list__item';

                const bar = document.createElement('div');
                bar.className = 'sg-list__bar';
                bar.innerHTML = '<span class="sg-list__n"></span>';
                bar.querySelector('.sg-list__n').textContent = '#' + (index + 1);

                [['↑', -1], ['↓', 1]].forEach(function (spec) {
                    const move = document.createElement('button');
                    move.type = 'button';
                    move.className = 'sg-list__move';
                    move.textContent = spec[0];
                    move.addEventListener('click', function () {
                        const to = index + spec[1];
                        if (to < 0 || to >= field.items.length) return;
                        const moved = field.items.splice(index, 1)[0];
                        field.items.splice(to, 0, moved);
                        onChange();
                        draw();
                    });
                    bar.appendChild(move);
                });

                const remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'sg-list__remove';
                remove.textContent = '×';
                remove.addEventListener('click', function () {
                    field.items.splice(index, 1);
                    onChange();
                    draw();
                });
                bar.appendChild(remove);
                row.appendChild(bar);

                row.appendChild(renderField(item, onChange));
                body.appendChild(row);
            });
        };
        draw();

        const add = document.createElement('button');
        add.type = 'button';
        add.className = 'button button-small button-secondary';
        add.textContent = 'Add';
        add.addEventListener('click', function () {
            field.items.push(blankFrom(field.child));
            onChange();
            draw();
        });
        wrap.appendChild(add);

        return wrap;
    }

    /*
     * A fresh copy of a schema, so two added rows never share state.
     *
     * A plain deep copy on purpose: the server already seeded every field
     * with its block's default (see field_schema), so a new section or list
     * row starts out valid and saveable.  Nulling the values here — which an
     * earlier version did — produced sections that failed validation the
     * instant they were added.
     */
    function blankFrom(schema) {
        return JSON.parse(JSON.stringify(schema));
    }

    // ── Schema → JSON ───────────────────────────────────────────────────

    function schemaValue(field) {
        if (field.kind === 'struct') {
            const out = {};
            (field.fields || []).forEach(function (child) {
                out[child.name] = schemaValue(child);
            });
            return out;
        }
        if (field.kind === 'list') {
            return (field.items || []).map(schemaValue);
        }
        return field.value === undefined ? null : field.value;
    }

    function refreshSummary(card) {
        // The server owns the authoritative summary; between saves just show
        // something honest rather than a stale one.
        card.summary = 'edited';
        renderGrid();
    }

    // ── Palette ─────────────────────────────────────────────────────────

    function buildPalette() {
        addMenu.innerHTML = '';
        DATA.palette.forEach(function (group) {
            const heading = document.createElement('h4');
            heading.textContent = group.group;
            addMenu.appendChild(heading);
            group.blocks.forEach(function (entry) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'sg-add__item';
                btn.textContent = entry.label;
                btn.addEventListener('click', function () {
                    addCard(entry);
                    toggleMenu(false);
                });
                addMenu.appendChild(btn);
            });
        });
    }

    function toggleMenu(open) {
        addMenu.hidden = !open;
        addToggle.setAttribute('aria-expanded', String(!!open));
    }

    function templateFor(type) {
        // The palette ships a blank schema for every block type, so a new
        // section is editable the moment it lands on the grid.
        for (let g = 0; g < DATA.palette.length; g++) {
            const match = DATA.palette[g].blocks.find(function (b) {
                return b.type === type;
            });
            if (match && match.schema) return blankFrom(match.schema);
        }
        const sibling = state.cards.find(function (c) { return c.type === type; });
        return sibling ? blankFrom(sibling.schema) : null;
    }

    function addCard(entry) {
        const id = uuid();
        const desktop = state.layout.desktop || (state.layout.desktop = {});
        const rows = Object.keys(desktop).map(function (k) {
            return desktop[k].row + desktop[k].row_span;
        });
        desktop[id] = {
            col: 1, span: 12,
            row: rows.length ? Math.max.apply(null, rows) : 1,
            row_span: 2, visible: true, style: 'plain', pad: 'md',
        };
        state.cards.push({
            id: id,
            type: entry.type,
            label: entry.label,
            icon: entry.icon,
            summary: 'New section',
            schema: templateFor(entry.type),
            isNew: true,
        });
        markDirty();
        renderGrid();
        select(id);
    }

    function duplicateCard(card) {
        const id = uuid();
        const p = placement(card.id);
        state.layout.desktop[id] = Object.assign({}, (state.layout.desktop || {})[card.id] || p, {
            row: p.row + p.row_span,
        });
        const copy = JSON.parse(JSON.stringify(card));
        copy.id = id;
        state.cards.push(copy);
        markDirty();
        renderGrid();
        select(id);
    }

    function deleteCard(card) {
        if (!window.confirm('Remove “' + card.label + '” from the homepage?')) return;
        state.cards = state.cards.filter(function (c) { return c.id !== card.id; });
        DATA.breakpoints.forEach(function (bp) {
            if (state.layout[bp]) delete state.layout[bp][card.id];
        });
        state.selected = null;
        markDirty();
        renderGrid();
        renderInspector();
    }

    // ── Saving ──────────────────────────────────────────────────────────

    function serialise() {
        const ordered = state.cards.slice().sort(function (a, b) {
            const pa = placement(a.id), pb = placement(b.id);
            return (pa.row - pb.row) || (pa.col - pb.col);
        });
        return ordered.map(function (card) {
            return {
                type: card.type,
                id: card.id,
                value: card.schema ? schemaValue(card.schema) : {},
            };
        });
    }

    function save(action) {
        setStatus(action === 'publish' ? 'Publishing…' : 'Saving…');
        return fetch(DATA.urls.save, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken(),
            },
            body: JSON.stringify({
                action: action,
                body: serialise(),
                layout: state.layout,
            }),
        })
            .then(function (response) {
                return response.json().then(function (payload) {
                    return { ok: response.ok, payload: payload };
                });
            })
            .then(function (result) {
                if (!result.ok || !result.payload.ok) {
                    setStatus(result.payload.error || 'Could not save.', 'error');
                    return false;
                }
                state.dirty = false;
                state.layout = result.payload.layout || state.layout;
                setStatus(result.payload.published
                    ? 'Published.'
                    : 'Draft saved. Publish when you are ready.', 'ok');
                return true;
            })
            .catch(function () {
                setStatus('Could not reach the server.', 'error');
                return false;
            });
    }

    // ── Wiring ──────────────────────────────────────────────────────────

    root.querySelectorAll('[data-sg-save]').forEach(function (btn) {
        btn.addEventListener('click', function () { save(btn.dataset.sgSave); });
    });

    root.querySelectorAll('.sg-bp').forEach(function (btn) {
        btn.addEventListener('click', function () {
            state.breakpoint = btn.dataset.bp;
            root.querySelectorAll('.sg-bp').forEach(function (other) {
                const active = other === btn;
                other.classList.toggle('is-active', active);
                other.setAttribute('aria-selected', String(active));
            });
            renderGrid();
            renderInspector();
        });
    });

    addToggle.addEventListener('click', function () {
        toggleMenu(addMenu.hidden);
    });
    document.addEventListener('click', function (event) {
        if (!addMenu.hidden && !addMenu.contains(event.target) && event.target !== addToggle) {
            toggleMenu(false);
        }
    });

    // Preview always saves first — an iframe of a stale draft is worse than
    // no preview, because it looks authoritative.
    root.querySelector('[data-sg-preview]').addEventListener('click', function () {
        save('draft').then(function (ok) {
            if (!ok) return;
            previewFrame.src = DATA.urls.preview + '?t=' + Date.now();
            previewPanel.hidden = false;
        });
    });
    root.querySelector('[data-sg-preview-close]').addEventListener('click', function () {
        previewPanel.hidden = true;
        previewFrame.src = 'about:blank';
    });

    const historyLink = root.querySelector('[data-sg-history]');
    if (historyLink) historyLink.href = DATA.urls.history;

    if (!DATA.canPublish) {
        const publishBtn = root.querySelector('[data-sg-save="publish"]');
        if (publishBtn) publishBtn.remove();
    }

    window.addEventListener('beforeunload', function (event) {
        if (!state.dirty) return;
        event.preventDefault();
        event.returnValue = '';
    });

    buildPalette();
    renderGrid();
    renderInspector();
})();
