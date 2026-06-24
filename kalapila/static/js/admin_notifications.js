(function () {
    document.addEventListener("DOMContentLoaded", function () {
        let container = document.getElementById("admin-toast-container");
        if (!container) {
            container = document.createElement("div");
            container.id = "admin-toast-container";
            document.body.appendChild(container);
        }

        // Retrieves the CSRF token from cookies or the DOM
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

        // Marks the notification as read on the server and dismisses the toast
        function markAsRead(notificationId, toastElement) {
            fetch(`/kalapila/admin/notifications/${notificationId}/read/`, {
                method: "POST",
                headers: { "X-CSRFToken": getCsrfToken() }
            })
            .then(res => { if (res.ok) dismissToast(toastElement, notificationId); })
            .catch(err => console.error("Error marking notification read:", err));
        }

        // Handles the removal of the toast element from the DOM
        function dismissToast(toastElement, notificationId) {
            toastElement.classList.add("admin-toast--removing");
            activeToastIds.delete(notificationId);
            setTimeout(() => toastElement.remove(), 350);
        }

        // Constructs and displays the notification toast
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

        let socket = null;
        let reconnectTimeout = null;

        function connectWebSocket() {
            // Determine the WebSocket protocol
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws/kalapila/admin/notifications/`;

            socket = new WebSocket(wsUrl);

            socket.onopen = function(e) {
                console.log('Connected to admin notifications WebSocket.');
                if (reconnectTimeout) {
                    clearTimeout(reconnectTimeout);
                    reconnectTimeout = null;
                }
                
                // Fetch initially to catch any missed notifications
                fetch("/kalapila/admin/notifications/")
                    .then(res => res.json())
                    .then(data => {
                        if (data && data.count > 0) {
                            data.notifications.forEach(showToast);
                        }
                    })
                    .catch(err => console.error("Error fetching initial dashboard notifications:", err));
            };

            socket.onmessage = function(e) {
                try {
                    const data = JSON.parse(e.data);
                    
                    // CORRECTION: Added a console log to help verify the exact structure of the WebSocket payload.
                    console.log("Admin WebSocket received:", data);

                    // CORRECTION: Broadened the validation condition. 
                    // Django Channels passes the event dictionary emitted by group_send.
                    // This allows the logic to trigger regardless of whether the developer 
                    // altered the type key to 'notification' or left it as 'send_notification', 
                    // or simply checking if the nested 'notification' object exists.
                    if (data.type === 'notification' || data.type === 'send_notification' || data.notification) {
                        showToast(data.notification);
                    }
                } catch (err) {
                    console.error('Error parsing WebSocket message:', err);
                }
            };

            socket.onclose = function(e) {
                console.warn('Admin notifications WebSocket closed. Reconnecting in 5s...');
                if (!reconnectTimeout) {
                    reconnectTimeout = setTimeout(connectWebSocket, 5000);
                }
            };

            socket.onerror = function(err) {
                console.error('WebSocket error:', err);
                socket.close();
            };
        }

        // Initialize WebSocket connection
        connectWebSocket();
    });
})();