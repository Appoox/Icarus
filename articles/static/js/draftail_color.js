/* ───────────────────────────────────────────────────────────────────────────
   Inline text colour for Wagtail's Draftail editor — dependency-free.

   Uses the React / Draft.js / Draftail globals that Wagtail's admin already
   exposes (window.React, window.DraftJS, window.draftail). No JSX, no build
   step, no npm packages. Registered from Python via an EntityFeature whose
   `js=[...]` loads this file.

   Behaviour: select some text, click the "A" toolbar button, pick a colour in
   the native OS colour dialog. The selection is wrapped in a COLOR entity that
   is stored as <span data-color="#rrggbb" style="color:#rrggbb">…</span>.
─────────────────────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  // Bail quietly if the expected admin globals aren't present, or if this file
  // somehow gets loaded twice.
  if (!window.draftail || !window.React || !window.DraftJS) return;
  if (window.__draftailColorRegistered) return;
  window.__draftailColorRegistered = true;

  var ENTITY_TYPE = 'COLOR';
  var DEFAULT_COLOR = '#c0392b';
  var DraftJS = window.DraftJS;

  // If the start of the selection already carries a COLOR entity, return its
  // colour so the picker opens pre-seeded with it. Fully defensive.
  function existingColor(editorState, selection) {
    try {
      var content = editorState.getCurrentContent();
      var block = content.getBlockForKey(selection.getStartKey());
      if (!block) return null;
      var offset = Math.max(0, Math.min(selection.getStartOffset(), block.getLength() - 1));
      var key = block.getEntityAt(offset);
      if (!key) return null;
      var entity = content.getEntity(key);
      if (!entity || entity.getType() !== ENTITY_TYPE) return null;
      return entity.getData().color || null;
    } catch (e) {
      return null;
    }
  }

  /* Source: mounted when the toolbar button is clicked. It opens a native
     <input type="color">, then applies the chosen colour to the selection. */
  function ColorSource() {
    window.React.Component.apply(this, arguments);
  }
  ColorSource.prototype = Object.create(window.React.Component.prototype);
  ColorSource.prototype.constructor = ColorSource;

  ColorSource.prototype.componentDidMount = function () {
    var props = this.props;
    var editorState = props.editorState;
    var entityType = props.entityType;
    var onComplete = props.onComplete;
    var onClose = props.onClose;
    var selection = editorState.getSelection();

    // Nothing to colour if the caret is collapsed — just close the control.
    if (selection.isCollapsed()) { onClose(); return; }

    var input = document.createElement('input');
    input.type = 'color';
    input.value = existingColor(editorState, selection) || DEFAULT_COLOR;
    input.setAttribute('aria-hidden', 'true');
    input.style.position = 'fixed';
    input.style.left = '-9999px';
    input.style.top = '0';
    input.style.opacity = '0';
    document.body.appendChild(input);

    var settled = false;
    function cleanup() {
      if (input.parentNode) input.parentNode.removeChild(input);
    }

    function apply() {
      if (settled) return;
      settled = true;
      var color = input.value;
      cleanup();

      var content = editorState.getCurrentContent();
      var withEntity = content.createEntity(entityType.type, 'MUTABLE', { color: color });
      var entityKey = withEntity.getLastCreatedEntityKey();
      var nextContent = DraftJS.Modifier.applyEntity(withEntity, selection, entityKey);
      var nextState = DraftJS.EditorState.push(editorState, nextContent, 'apply-entity');
      nextState = DraftJS.EditorState.forceSelection(nextState, selection);
      onComplete(nextState);
    }

    function cancel() {
      if (settled) return;
      settled = true;
      cleanup();
      onClose();
    }

    // `change` fires when a colour is committed. There is no cancel event for
    // <input type="color">, so we treat "focus returned to the window without a
    // change" as a cancel, giving `change` a moment to land first.
    input.addEventListener('change', apply);
    function onWindowFocus() {
      window.removeEventListener('focus', onWindowFocus);
      window.setTimeout(function () { if (!settled) cancel(); }, 250);
    }
    window.addEventListener('focus', onWindowFocus);

    input.click();
  };

  ColorSource.prototype.render = function () { return null; };

  /* Decorator: how a coloured span looks inside the editor. */
  function ColorSpan(props) {
    var data = props.contentState.getEntity(props.entityKey).getData();
    return window.React.createElement(
      'span',
      { style: { color: data.color }, 'data-color': data.color },
      props.children
    );
  }

  window.draftail.registerPlugin({
    type: ENTITY_TYPE,
    source: ColorSource,
    decorator: ColorSpan,
  }, 'entityTypes');
})();
