/* ─────────────────────────────────────────────────────────────────
   postbox_admin_notifications.js
   Real-time postbox toast notifications for Wagtail admin staff.

   Mirrors admin_notifications.js (kalapila) but:
     • Connects to  /ws/postbox/admin/notifications/
     • Fetches from /postbox/admin/notifications/
     • Marks read via POST /postbox/admin/notifications/{id}/read/
     • Reuses #admin-toast-container and .admin-toast-* classes
       (loaded globally by kalapila's insert_global_admin_css hook)
───────────────────────────────────────────────────────────────── */

(function () {
    var i18nEl = document.getElementById('postbox-toast-i18n');
        var L = i18nEl ? JSON.parse(i18nEl.textContent) : {};

        function safeUrl(u) {
            if (!u) return '#';
            try {
                var parsed = new URL(u, window.location.origin);
                return (parsed.protocol === 'http:' || parsed.protocol === 'https:') ? parsed.href : '#';
            } catch (e) { return '#'; }
        }
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {

        /* ── Toast container — shared with kalapila's comment toasts ── */
        var container = document.getElementById('admin-toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'admin-toast-container';
            document.body.appendChild(container);
        }

        /* ── CSRF helper ─────────────────────────────────────────────── */
        function getCsrf() {
            var val = null;
            if (document.cookie) {
                document.cookie.split(';').forEach(function (c) {
                    c = c.trim();
                    if (c.startsWith('csrftoken=')) {
                        val = decodeURIComponent(c.slice(10));
                    }
                });
            }
            if (!val) {
                var el = document.querySelector('[name=csrfmiddlewaretoken]');
                if (el) val = el.value;
            }
            return val;
        }

        /* ── Active toast set (prevents duplicates) ──────────────────── */
        var activeIds = new Set();

        /* ── Mark notification read + dismiss ────────────────────────── */
        function markRead(id, toastEl) {
            fetch('/postbox/admin/notifications/' + id + '/read/', {
                method: 'POST',
                headers: { 'X-CSRFToken': getCsrf() },
            })
            .then(function (r) {
                if (r.ok) dismiss(toastEl, id);
            })
            .catch(function (err) {
                console.error('postbox-notifications: mark-read error', err);
            });
        }

        function dismiss(toastEl, id) {
            toastEl.classList.add('admin-toast--removing');
            activeIds.delete(id);
            setTimeout(function () { toastEl.remove(); }, 350);
        }

        /* ── Build and show a toast ──────────────────────────────────── */
        function showToast(notification) {
            if (activeIds.has(notification.id)) return;
            activeIds.add(notification.id);

            var toast = document.createElement('div');
            toast.className = 'admin-toast';
            toast.id = 'postbox-toast-' + notification.id;

            var header = document.createElement('div');
            header.className = 'admin-toast__header';
            var title = document.createElement('h4');
            title.className = 'admin-toast__title';
            title.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5" style="display:inline-block;vertical-align:middle;margin-right:5px;"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
            title.appendChild(document.createTextNode(' ' + (L.newFeedback || 'New Feedback')));
            var closeBtn = document.createElement('button');
            closeBtn.className = 'admin-toast__close-btn';
            closeBtn.setAttribute('aria-label', L.close || 'Close');
            closeBtn.innerHTML = '&times;';
            header.appendChild(title);
            header.appendChild(closeBtn);

            var body = document.createElement('div');
            body.className = 'admin-toast__body';
            body.textContent = notification.feedback || '';   // reader text → textContent

            var footer = document.createElement('div');
            footer.className = 'admin-toast__footer';
            var reviewLink = document.createElement('a');
            reviewLink.className = 'admin-toast__action-link';
            reviewLink.href = safeUrl(notification.url);
            reviewLink.textContent = L.review || 'Review';
            var dismissBtn = document.createElement('button');
            dismissBtn.className = 'admin-toast__dismiss-btn';
            dismissBtn.textContent = L.dismiss || 'Dismiss';
            footer.appendChild(reviewLink);
            footer.appendChild(dismissBtn);

            toast.appendChild(header);
            toast.appendChild(body);
            toast.appendChild(footer);
            container.appendChild(toast);

            var timer = setTimeout(function () { markRead(notification.id, toast); }, 15000);
            toast.addEventListener('mouseenter', function () { clearTimeout(timer); });
            toast.addEventListener('mouseleave', function () {
                timer = setTimeout(function () { markRead(notification.id, toast); }, 8000);
            });
            closeBtn.addEventListener('click', function () { markRead(notification.id, toast); });
            reviewLink.addEventListener('click', function () { markRead(notification.id, toast); });
            dismissBtn.addEventListener('click', function () { markRead(notification.id, toast); });
        }

        /* ── WebSocket connection ─────────────────────────────────────── */
        var socket          = null;
        var reconnectTimer  = null;

        function connect() {
            var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            socket = new WebSocket(
                proto + '//' + window.location.host + '/ws/postbox/admin/notifications/'
            );

            socket.onopen = function () {
                if (reconnectTimer) {
                    clearTimeout(reconnectTimer);
                    reconnectTimer = null;
                }
                /* Fetch any unread notifications that arrived while offline */
                fetch('/postbox/admin/notifications/')
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data && data.count > 0) {
                            data.notifications.forEach(showToast);
                        }
                    })
                    .catch(function (err) {
                        console.error('postbox-notifications: initial fetch error', err);
                    });
            };

            socket.onmessage = function (e) {
                try {
                    var data = JSON.parse(e.data);
                    if (data.type === 'notification' && data.notification) {
                        showToast(data.notification);
                    }
                } catch (err) {
                    console.error('postbox-notifications: parse error', err);
                }
            };

            socket.onclose = function () {
                if (!reconnectTimer) {
                    reconnectTimer = setTimeout(connect, 5000);
                }
            };

            socket.onerror = function () {
                socket.close();
            };
        }

        connect();
    });

}());