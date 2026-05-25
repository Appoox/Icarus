(function () {
    // Check if we are logged into the admin dashboard and notifications container is set up
    document.addEventListener("DOMContentLoaded", function () {
        // Ensure container exists
        let container = document.getElementById("admin-toast-container");
        if (!container) {
            container = document.createElement("div");
            container.id = "admin-toast-container";
            document.body.appendChild(container);
        }

        // Helper to get CSRF from cookies
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
            // Fallback to hidden input if cookies not readable
            if (!cookieValue) {
                const input = document.querySelector('[name=csrfmiddlewaretoken]');
                if (input) cookieValue = input.value;
            }
            return cookieValue;
        }

        // Keep track of displayed toast IDs
        const activeToastIds = new Set();

        // Mark notification as read
        function markAsRead(notificationId, toastElement) {
            fetch(`/kalapila/admin/notifications/${notificationId}/read/`, {
                method: "POST",
                headers: {
                    "X-CSRFToken": getCsrfToken()
                }
            })
            .then(res => {
                if (res.ok) {
                    dismissToast(toastElement, notificationId);
                }
            })
            .catch(err => console.error("Error marking notification read:", err));
        }

        // Dismiss Toast UI helper
        function dismissToast(toastElement, notificationId) {
            toastElement.classList.add("admin-toast--removing");
            activeToastIds.delete(notificationId);
            setTimeout(() => {
                toastElement.remove();
            }, 350);
        }

        // Show Toast
        function showToast(notification) {
            if (activeToastIds.has(notification.id)) return;
            activeToastIds.add(notification.id);

            const toast = document.createElement("div");
            toast.className = "admin-toast";
            toast.id = `toast-${notification.id}`;

            toast.innerHTML = `
                <div class="admin-toast__header">
                    <h4 class="admin-toast__title">
                        <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2.5" fill="none" style="display:inline-block; vertical-align:middle;"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
                        പുതിയ അഭിപ്രായം!
                    </h4>
                    <button class="admin-toast__close-btn" aria-label="Close">&times;</button>
                </div>
                <div class="admin-toast__body">
                    ${notification.message}
                </div>
                <div class="admin-toast__footer">
                    <button class="admin-toast__dismiss-btn">Dismiss</button>
                    <a href="${notification.url}" class="admin-toast__action-link" target="_blank">Moderate</a>
                </div>
            `;

            container.appendChild(toast);

            // Setup Auto-dismiss after 15 seconds (pause if hovered)
            let dismissTimeout = setTimeout(() => {
                markAsRead(notification.id, toast);
            }, 15000);

            toast.addEventListener("mouseenter", function () {
                clearTimeout(dismissTimeout);
            });

            toast.addEventListener("mouseleave", function () {
                dismissTimeout = setTimeout(() => {
                    markAsRead(notification.id, toast);
                }, 8000); // 8 seconds after leaving hover
            });

            // Action: Close/X Button
            toast.querySelector(".admin-toast__close-btn").addEventListener("click", function () {
                markAsRead(notification.id, toast);
            });

            // Action: Dismiss Button
            toast.querySelector(".admin-toast__dismiss-btn").addEventListener("click", function () {
                markAsRead(notification.id, toast);
            });

            // Action: Moderate Link (Clicking marks read & goes to link)
            toast.querySelector(".admin-toast__action-link").addEventListener("click", function () {
                markAsRead(notification.id, toast);
            });
        }

        // Fetch Notifications Function
        function pollNotifications() {
            fetch("/kalapila/admin/notifications/")
                .then(res => {
                    if (res.status === 403 || res.status === 401) {
                        // User not authenticated or not staff; stop polling
                        clearInterval(pollInterval);
                        return null;
                    }
                    return res.json();
                })
                .then(data => {
                    if (data && data.notifications) {
                        data.notifications.forEach(showToast);
                    }
                })
                .catch(err => {
                    console.error("Error polling dashboard notifications:", err);
                });
        }

        // Start polling immediately, then every 20 seconds
        pollNotifications();
        const pollInterval = setInterval(pollNotifications, 20000);
    });
})();
