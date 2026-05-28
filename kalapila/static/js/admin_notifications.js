(function () {
    document.addEventListener("DOMContentLoaded", function () {
        let container = document.getElementById("admin-toast-container");
        if (!container) {
            container = document.createElement("div");
            container.id = "admin-toast-container";
            document.body.appendChild(container);
        }

        function getCsrfToken() {
            let cookieValue = null;
            if (document.cookie && document.cookie !== '') {
                const cookies = document.cookie.split(';');
                for (let i = 0; i < cookies.length; i++) {
                    const cookie = cookies[i].trim();
                    if (cookie.substring(0, 10) === ('csrftoken=')) {
                        cookieValue = decodeURIComponent(cookie.substring(10));
                        break;
                    }
                }
            }
            if (!cookieValue) {
                const input = document.querySelector('[name=csrfmiddlewaretoken]');
                if (input) cookieValue = input.value;
            }
            return cookieValue;
        }

        const activeToastIds = new Set();

        function markAsRead(notificationId, toastElement) {
            fetch(`/kalapila/admin/notifications/${notificationId}/read/`, {
                method: "POST",
                headers: { "X-CSRFToken": getCsrfToken() }
            })
            .then(res => { if (res.ok) dismissToast(toastElement, notificationId); })
            .catch(err => console.error("Error marking notification read:", err));
        }

        function dismissToast(toastElement, notificationId) {
            toastElement.classList.add("admin-toast--removing");
            activeToastIds.delete(notificationId);
            setTimeout(() => toastElement.remove(), 350);
        }

        function showToast(notification) {
            if (activeToastIds.has(notification.id)) return;
            activeToastIds.add(notification.id);

            const toast = document.createElement("div");
            toast.className = "admin-toast";
            toast.id = `toast-${notification.id}`;
            // Inside your showToast(notification) function template mapping:
            toast.innerHTML = `
                <div class="admin-toast__header">
                    <h4 class="admin-toast__title">
                        <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2.5" fill="none" style="display:inline-block; vertical-align:middle;"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
                        പുതിയ അഭിപ്രായം!
                    </h4>
                    <button class="admin-toast__close-btn" aria-label="Close">&times;</button>
                </div>
                <div class="admin-toast__body">${notification.message}</div>
                <div class="admin-toast__footer">
                    <a href="${notification.page_url || '#'}" class="admin-toast__view-btn" target="_blank">View</a>
                    <button class="admin-toast__dismiss-btn">Dismiss</button>
                    <a href="${notification.url}" class="admin-toast__action-link" target="_blank">Moderate</a>
                </div>
            `;
            container.appendChild(toast);

            let dismissTimeout = setTimeout(() => markAsRead(notification.id, toast), 15000);
            toast.addEventListener("mouseenter", () => clearTimeout(dismissTimeout));
            toast.addEventListener("mouseleave", () => {
                dismissTimeout = setTimeout(() => markAsRead(notification.id, toast), 8000);
            });
            toast.querySelector(".admin-toast__close-btn").addEventListener("click", () => markAsRead(notification.id, toast));
            toast.querySelector(".admin-toast__view-btn").addEventListener("click", () => markAsRead(notification.id, toast));
            toast.querySelector(".admin-toast__dismiss-btn").addEventListener("click", () => markAsRead(notification.id, toast));
            toast.querySelector(".admin-toast__action-link").addEventListener("click", () => markAsRead(notification.id, toast));
        }

        let pollInterval = setInterval(pollNotifications, 30000);
        let isStopped = false;   // permanent stop on 401/403

        function pollNotifications() {
            // Skip the network call entirely if the tab is hidden
            if (document.hidden) return;

            fetch("/kalapila/admin/notifications/")
                .then(res => {
                    if (res.status === 403 || res.status === 401) {
                        // Not staff or logged out — stop polling for this session
                        isStopped = true;
                        clearInterval(pollInterval);
                        return null;
                    }
                    return res.json();
                })
                .then(data => {
                    // count === 0 means nothing to show; skip iteration entirely
                    if (data && data.count > 0) {
                        data.notifications.forEach(showToast);
                    }
                })
                .catch(err => console.error("Error polling dashboard notifications:", err));
        }

        // Resume polling when the tab becomes visible again
        document.addEventListener("visibilitychange", function () {
            if (!isStopped && !document.hidden) {
                // Immediate check on tab focus so staff don't wait 20s after switching back
                pollNotifications();
            }
        });

        pollNotifications();
        pollInterval = setInterval(pollNotifications, 20000);
    });
})();