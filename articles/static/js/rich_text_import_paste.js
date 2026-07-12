/*
 * rich_text_import_paste.js
 * -------------------------
 * A plain <textarea> only receives the clipboard's text/plain payload on paste,
 * which throws away every heading, paragraph break, image and bold/italic run —
 * that is why the importer could not separate paragraphs. This handler grabs the
 * clipboard's *text/html* payload instead and drops the real HTML source into
 * the import textarea, so the server-side converter has structure to work with.
 *
 * A single capturing listener on `document` is used (rather than binding to each
 * textarea) so it also covers StreamField import blocks that are added to the
 * page dynamically after load.
 *
 * If the clipboard has no HTML (genuine plain text), we do nothing and let the
 * browser paste normally — the converter's plain-text path handles that.
 */
(function () {
  "use strict";

  var SELECTOR = "textarea.rich-text-import-input, textarea[data-rich-text-import]";

  function insertAtCursor(textarea, text) {
    var start = textarea.selectionStart != null ? textarea.selectionStart : textarea.value.length;
    var end = textarea.selectionEnd != null ? textarea.selectionEnd : textarea.value.length;
    var before = textarea.value.slice(0, start);
    var after = textarea.value.slice(end);
    textarea.value = before + text + after;
    var caret = start + text.length;
    textarea.selectionStart = textarea.selectionEnd = caret;
    // Notify Wagtail/Draftail-adjacent listeners that the field changed.
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function onPaste(event) {
    var textarea = event.target;
    if (!textarea || textarea.tagName !== "TEXTAREA") return;
    if (!textarea.matches(SELECTOR)) return;

    var clipboard = event.clipboardData || window.clipboardData;
    if (!clipboard) return;

    var html = "";
    try {
      html = clipboard.getData("text/html") || "";
    } catch (e) {
      html = "";
    }
    if (!html.trim()) return; // no rich content — let the browser paste plain text

    event.preventDefault();
    insertAtCursor(textarea, html);
  }

  // Capture phase so dynamically-added blocks are covered without re-binding.
  document.addEventListener("paste", onPaste, true);
})();
