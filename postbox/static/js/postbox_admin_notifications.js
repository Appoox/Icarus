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
            toast.innerHTML = [
                '<div class="admin-toast__header">',
                '  <h4 class="admin-toast__title">',
                '    <svg viewBox="0 0 24 24" width="15" height="15" fill="none"',
                '         stroke="currentColor" stroke-width="2.5"',
                '         style="display:inline-block;vertical-align:middle;margin-right:5px;">',
                '      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
                '    </svg>',
                '    New Feedback',
                '  </h4>',
                '  <button class="admin-toast__close-btn" aria-label="Close">&times;</button>',
                '</div>',
                '<div class="admin-toast__body">' + notification.feedback + '</div>',
                '<div class="admin-toast__footer">',
                '  <a href="' + (notification.url || '#') + '"',
                '     class="admin-toast__action-link">Review</a>',
                '  <button class="admin-toast__dismiss-btn">Dismiss</button>',
                '</div>',
            ].join('');

            container.appendChild(toast);

            /* Auto-dismiss after 15 s; pause on hover */
            var timer = setTimeout(function () {
                markRead(notification.id, toast);
            }, 15000);

            toast.addEventListener('mouseenter', function () {
                clearTimeout(timer);
            });
            toast.addEventListener('mouseleave', function () {
                timer = setTimeout(function () {
                    markRead(notification.id, toast);
                }, 8000);
            });

            toast.querySelector('.admin-toast__close-btn')
                 .addEventListener('click', function () {
                     markRead(notification.id, toast);
                 });
            toast.querySelector('.admin-toast__dismiss-btn')
                 .addEventListener('click', function () {
                     markRead(notification.id, toast);
                 });
            toast.querySelector('.admin-toast__action-link')
                 .addEventListener('click', function () {
                     markRead(notification.id, toast);
                 });
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