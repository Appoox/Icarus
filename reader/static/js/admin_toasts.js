/**
 * admin_toasts.js
 *
 * Converts Wagtail 7.4 admin messages into floating toast notifications.
 *
 * How it works
 * ────────────
 * The CSS file hides [data-w-messages-target="container"] immediately on
 * paint, so no yellow banner ever flashes. This JS then reads all <li>
 * items out of that hidden container, creates toast divs, and removes
 * the originals from the DOM.
 *
 * A MutationObserver watches the container for items added dynamically
 * (e.g. "Page saved successfully" after admin save actions), so those
 * also appear as toasts rather than the native Wagtail banner.
 *
 * Wagtail 7.4 message HTML structure (from base.html):
 *   <div class="messages" data-controller="w-messages" …>
 *     <ul data-w-messages-target="container">      ← we watch this
 *       <li class="warning">
 *         <svg class="icon messages-icon">…</svg>  ← stripped from toast
 *         …message text…
 *       </li>
 *     </ul>
 *   </div>
 */

(function () {
    'use strict';

    // Confirm the script loaded — remove this log once confirmed working
    console.log('[admin_toasts] script loaded');

    // ── i18n ──────────────────────────────────────────────────────────────

    /** Reads translated labels from the json_script holder injected by the hook. */
    function i18nLabels() {
        try {
            var holder = document.getElementById('toast-i18n');
            if (holder) {
                return JSON.parse(holder.textContent) || {};
            }
        } catch (e) {
            /* malformed holder — fall through to defaults */
        }
        return {};
    }

    var LABELS = {};

    // ── Helpers ───────────────────────────────────────────────────────────

    /** Returns or creates the fixed toast container in the top-right corner. */
    function getContainer() {
        let c = document.querySelector('.custom-admin-toasts-container');
        if (!c) {
            c = document.createElement('div');
            c.className = 'custom-admin-toasts-container';
            document.body.appendChild(c);
        }
        return c;
    }

    /** Fades a toast out and removes it. */
    function dismiss(toast) {
        if (!toast.parentNode) return;
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-10px)';
        setTimeout(function () { if (toast.parentNode) toast.remove(); }, 500);
    }

    /**
     * Determines the toast colour class from a Wagtail message <li>.
     *
     * Classification is by the li's class ONLY. The previous version also
     * sniffed the message text for English substrings ('incomplete',
     * 'expired') — that silently stopped matching the moment messages were
     * translated to Malayalam. Django's message level → class mapping is the
     * single source of truth and is language-independent.
     *
     * @param  {Element} li
     * @returns {'warning'|'error'|'info'}
     */
    function toastClass(li) {
        const cls = li.className || '';
        if (cls.includes('error') || cls.includes('critical')) return 'error';
        if (cls.includes('warning'))                           return 'warning';
        return 'info';
    }

    /**
     * Extracts user-visible HTML from a Wagtail message <li>,
     * stripping the injected SVG icon so it doesn't appear raw in the toast.
     * @param  {Element} li
     * @returns {string}
     */
    function extractHtml(li) {
        const clone = li.cloneNode(true);
        // Remove Wagtail's injected icon SVGs
        clone.querySelectorAll('.messages-icon, svg.icon').forEach(function (el) { el.remove(); });
        // Also remove the "Go to first error" button (only relevant in-page)
        clone.querySelectorAll('button').forEach(function (el) { el.remove(); });
        return clone.innerHTML.trim();
    }

    /**
     * Converts a single <li> message element into a floating toast.
     * Removes the original element to prevent Wagtail's Stimulus controller
     * from re-showing it.
     * @param {Element} li
     */
    function processMessage(li) {
        // Guard: already handled (MutationObserver can fire multiple times)
        if (li.dataset.toastHandled) return;
        li.dataset.toastHandled = 'true';

        // Skip inline field validation errors inside panel forms
        if (li.closest('.field') || li.closest('.form-row')) return;

        // Check if this is a persistent confirm warning modal
        const modal = li.querySelector('.last-edited-warning-modal');
        if (modal) {
            document.body.appendChild(modal);
            li.remove();
            return;
        }

        const html = extractHtml(li);
        if (!html) return;

        // Remove from DOM — prevents Stimulus "appear" from re-showing it
        li.remove();

        // Build toast
        const toast = document.createElement('div');
        toast.className = 'custom-admin-toast ' + toastClass(li);
        toast.innerHTML = html;

        // Close button
        const btn = document.createElement('button');
        btn.className = 'close-btn';
        btn.setAttribute('aria-label', LABELS.dismiss || 'നീക്കം ചെയ്യുക');
        btn.innerHTML = '&times;';
        btn.addEventListener('click', function () { dismiss(toast); });
        toast.appendChild(btn);

        getContainer().appendChild(toast);

        // Auto-dismiss after 6 s
        setTimeout(function () { dismiss(toast); }, 6000);
    }

    // ── Main ──────────────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', function () {
        console.log('[admin_toasts] DOMContentLoaded fired');

        // Read translated labels injected by the wagtail_hooks json_script
        LABELS = i18nLabels();

        // Primary target: Wagtail 7.4 Stimulus messages container
        const ul = document.querySelector('[data-w-messages-target="container"]');

        if (ul) {
            console.log('[admin_toasts] found message container, items:', ul.querySelectorAll('li').length);

            // Process any server-rendered items already in the container
            ul.querySelectorAll('li').forEach(processMessage);

            // Watch for dynamically added items (post-save success toasts, etc.)
            // subtree:true + attributes:true catches both new <li> children AND
            // class changes Stimulus makes on existing ones (the "appear" stamp).
            new MutationObserver(function (mutations) {
                mutations.forEach(function (mutation) {
                    // New <li> nodes added by Stimulus dynamically
                    mutation.addedNodes.forEach(function (node) {
                        if (node.nodeType === Node.ELEMENT_NODE && node.tagName === 'LI') {
                            processMessage(node);
                        }
                    });
                    // Class attribute changed on an existing <li> (Stimulus "appear")
                    if (
                        mutation.type === 'attributes' &&
                        mutation.attributeName === 'class' &&
                        mutation.target.tagName === 'LI'
                    ) {
                        processMessage(mutation.target);
                    }
                });
            }).observe(ul, {
                childList:       true,
                subtree:         true,   // watch <li> children, not just the <ul>
                attributes:      true,
                attributeFilter: ['class'],
            });

        } else {
            // Fallback for older Wagtail rendering without Stimulus
            console.log('[admin_toasts] Stimulus container not found, trying fallback selector');
            document.querySelectorAll('.messages li').forEach(processMessage);
        }
    });

}());