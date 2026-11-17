/* ───────────────────────────────────────────────────────────────────────────
   "Glossary term" for Wagtail's Draftail editor — dependency-free, like
   articles/static/js/draftail_color.js: it uses the React / Draft.js /
   Draftail globals the admin already has. No JSX, no build step.

   Select a word, click the toolbar button and pick the term in Wagtail's own
   chooser. The selection becomes a GLOSSARY entity, stored as

       <a linktype="glossary" id="12">…</a>          (glossary/rich_text.py)

   The selected word need not be the term as the glossary spells it — an
   inflected form is fine. With nothing selected, the term's name is typed in.
   In the editor a marked word is underlined; clicking it names its term and
   offers to change or remove the mark.
─────────────────────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  var draftail = window.draftail;
  var React = window.React;
  var DraftJS = window.DraftJS;
  // Bail quietly if the admin's globals aren't there, or on a second load.
  if (!draftail || !React || !DraftJS || !draftail.ModalWorkflowSource) return;
  if (window.__draftailGlossaryRegistered) return;
  window.__draftailGlossaryRegistered = true;

  var ENTITY_TYPE = 'GLOSSARY';

  class GlossarySource extends draftail.ModalWorkflowSource {
    getChooserConfig() {
      var urls = (this.props.entityType && this.props.entityType.chooserUrls) || {};
      return {
        url: urls.glossaryChooser,
        urlParams: {},
        onload: window.CHOOSER_MODAL_ONLOAD_HANDLERS,
        responses: { chosen: this.onChosen },
      };
    }

    filterEntityData(data) {
      return { id: Number(data.id), string: data.string };
    }

    // Applied here rather than by the base class, which only knows Wagtail's
    // own entity types: it has no mutability for GLOSSARY, and with nothing
    // selected it types a link's title, which a term doesn't have.
    onChosen(data) {
      var props = this.props;
      var editorState = props.editorState;
      var selection = editorState.getSelection();
      var content = editorState.getCurrentContent().createEntity(
        props.entityType.type, 'MUTABLE', this.filterEntityData(data));
      var entityKey = content.getLastCreatedEntityKey();
      var next;
      if (selection.isCollapsed()) {
        // "പദം (Term)" is how a term is listed; type just the word.
        var word = String(data.string || '').replace(/\s*\([^()]*\)\s*$/, '');
        next = DraftJS.EditorState.push(
          editorState,
          DraftJS.Modifier.insertText(content, selection, word, null, entityKey),
          'insert-characters');
      } else {
        next = DraftJS.RichUtils.toggleLink(editorState, selection, entityKey);
      }
      this.workflow.close();
      props.onComplete(next);
    }
  }

  /* How a marked word looks in the editor: Wagtail's link-style tooltip. */
  function GlossaryTerm(props) {
    var data = props.contentState.getEntity(props.entityKey).getData();
    return React.createElement(draftail.TooltipEntity, Object.assign({}, props, {
      icon: '#icon-openquote',
      label: data.string || 'This term is no longer in the glossary',
    }));
  }

  draftail.registerPlugin({
    type: ENTITY_TYPE,
    source: GlossarySource,
    decorator: GlossaryTerm,
  }, 'entityTypes');
})();
