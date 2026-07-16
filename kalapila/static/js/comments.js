document.addEventListener("DOMContentLoaded", function () {
    const commentsWrapper = document.getElementById("comments-wrapper");
    if (!commentsWrapper) return;

    const pageId = commentsWrapper.getAttribute("data-page-id");
    const mainForm = document.getElementById("main-comment-form");
    const commentsList = document.getElementById("comments-list-container");
    const emptyState = document.getElementById("comments-empty-message");
    const countBadge = document.getElementById("comment-count-badge");

    // Malayalam labels emitted as data-i18n-* attributes on #comments-wrapper
    const T = commentsWrapper.dataset;

    function getCsrfToken() {
        const el = document.querySelector('[name=csrfmiddlewaretoken]');
        if (el) return el.value;
        const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    // Helper: update comment count badge
    function updateCommentCount(offset) {
        if (countBadge) {
            let current = parseInt(countBadge.textContent) || 0;
            countBadge.textContent = Math.max(0, current + offset);
        }
    }

    // Client-Side Permission Corrector: Adjusts button states for WebSocket-injected comments
    function adjustCommentButtons(commentCard) {
        if (!commentCard) return;

        // Extract credentials from global wrapper and card attributes
        const currentUserId = commentsWrapper.getAttribute("data-current-user-id");
        const isStaff = commentsWrapper.getAttribute("data-is-staff") === "true";
        const isSuperuser = commentsWrapper.getAttribute("data-is-superuser") === "true";
        const authorId = commentCard.getAttribute("data-author-id");
        const commentId = commentCard.getAttribute("data-comment-id");

        const actionsContainer = commentCard.querySelector(".comment-card__actions");
        if (!actionsContainer) return;

        let deleteBtn = actionsContainer.querySelector(".delete-comment-btn");
        let reportBtn = actionsContainer.querySelector(".report-comment-btn");

        // If the viewing user is anonymous/not authenticated, remove operational buttons
        if (!currentUserId) {
            if (deleteBtn) deleteBtn.remove();
            if (reportBtn) reportBtn.remove();
            return;
        }

        const isAuthor = (currentUserId === authorId);
        const canDelete = isAuthor || isStaff || isSuperuser;

        // Correct Delete button presence matching actual permissions
        if (canDelete) {
            if (!deleteBtn) {
                deleteBtn = document.createElement("button");
                deleteBtn.className = "comment-action-btn delete-comment-btn";
                deleteBtn.setAttribute("data-url", `/kalapila/comment/${commentId}/delete/`);
                deleteBtn.innerHTML = `<i class="far fa-trash-alt"></i> ${T.i18nDelete}`;

                if (reportBtn) {
                    actionsContainer.insertBefore(deleteBtn, reportBtn);
                } else {
                    actionsContainer.appendChild(deleteBtn);
                }
            }
        } else {
            if (deleteBtn) deleteBtn.remove();
        }

        // Correct Report button presence matching actual permissions
        if (!isAuthor) {
            if (!reportBtn) {
                reportBtn = document.createElement("button");
                reportBtn.className = "comment-action-btn report-comment-btn";
                reportBtn.setAttribute("data-url", `/kalapila/comment/${commentId}/report/`);
                reportBtn.innerHTML = `<i class="far fa-flag"></i> ${T.i18nReport}`;
                actionsContainer.appendChild(reportBtn);
            }
        } else {
            if (reportBtn) reportBtn.remove();
        }
    }

    // Character Counter Logic
    function initCharCounter(textarea, counterSpan, submitBtn) {
        textarea.addEventListener("input", function () {
            const len = textarea.value.length;
            counterSpan.textContent = len;

            if (len >= 2950) {
                counterSpan.parentElement.className = "char-counter danger";
            } else if (len >= 2700) {
                counterSpan.parentElement.className = "char-counter warning";
            } else {
                counterSpan.parentElement.className = "char-counter";
            }

            if (len > 3000) {
                submitBtn.disabled = true;
            } else {
                submitBtn.disabled = false;
            }
        });
    }

    // Initialize counter for the main form
    if (mainForm) {
        const mainTextarea = mainForm.querySelector(".comment-textarea");
        const mainCounter = mainForm.querySelector(".char-count-current");
        const mainSubmit = mainForm.querySelector(".submit-comment-btn");
        initCharCounter(mainTextarea, mainCounter, mainSubmit);
    }

    // WebSocket for Real-time Comments
    let commentSocket = null;
    let commentReconnectTimeout = null;

    function connectCommentWebSocket() {
        if (!pageId) return;
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/kalapila/page/${pageId}/comments/`;

        commentSocket = new WebSocket(wsUrl);

        commentSocket.onopen = function(e) {
            console.log('Connected to comments WebSocket.');
            if (commentReconnectTimeout) {
                clearTimeout(commentReconnectTimeout);
                commentReconnectTimeout = null;
            }
        };

        commentSocket.onmessage = function(e) {
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'new_comment') {
                    // Prevent duplicate if this client submitted it via AJAX and already rendered it
                    // NOTE: This acts as a fallback failsafe, but the race condition is resolved
                    // since the AJAX handler no longer injects HTML directly.
                    if (document.getElementById(`comment-${data.comment_id}`)) return;

                    if (emptyState) {
                        emptyState.style.display = "none";
                    }

                    if (data.parent_id) {
                        const parentCard = document.getElementById(`comment-${data.parent_id}`);
                        if (parentCard && data.html) {
                            const parentRepliesList = parentCard.querySelector(".replies-list");
                            parentRepliesList.insertAdjacentHTML("beforeend", data.html);
                            // Run the button visibility corrector on the appended child node
                            adjustCommentButtons(parentRepliesList.lastElementChild);
                        }
                    } else if (data.html) {
                        commentsList.insertAdjacentHTML("beforeend", data.html);
                        // Run the button visibility corrector on the appended child node
                        adjustCommentButtons(commentsList.lastElementChild);
                    }
                    // Update comment count is handled strictly by the WebSocket now to avoid double counting
                    updateCommentCount(1);
                } else if (data.type === 'delete_comment') {
                    const commentCard = document.getElementById(`comment-${data.comment_id}`);
                    if (commentCard) {
                        commentCard.style.transition = "opacity 0.3s ease, transform 0.3s ease";
                        commentCard.style.opacity = "0";
                        commentCard.style.transform = "translateY(5px)";
                        setTimeout(() => {
                            commentCard.remove();
                            if (commentsList.children.length === 0 && emptyState) {
                                emptyState.style.display = "block";
                            }
                        }, 300);
                        updateCommentCount(-1);
                    }
                }
            } catch (err) {
                console.error('Error parsing WebSocket message:', err);
            }
        };

        commentSocket.onclose = function(e) {
            console.warn('Comments WebSocket closed. Reconnecting in 5s...');
            if (!commentReconnectTimeout) {
                commentReconnectTimeout = setTimeout(connectCommentWebSocket, 5000);
            }
        };

        commentSocket.onerror = function(err) {
            console.error('WebSocket error:', err);
            commentSocket.close();
        };
    }

    connectCommentWebSocket();

    // Post comment / reply handler
    async function submitComment(form, isReply = false) {
        const formData = new FormData(form);
        const submitBtn = form.querySelector("button[type=submit]");
        submitBtn.disabled = true;

        try {
            const response = await fetch("/kalapila/post/", {
                method: "POST",
                body: formData,
                headers: {
                    "X-CSRFToken": getCsrfToken()
                }
            });

            const data = await response.json();
            if (response.ok && data.status === "success") {
                // UI CLEANUP ONLY: The WebSocket onmessage listener will handle the actual
                // DOM insertion and comment count increment to prevent race conditions.

                // If comment list has an empty state, remove it
                if (emptyState) {
                    emptyState.style.display = "none";
                }

                if (isReply) {
                    // Just remove the inline reply form. The WS will inject the comment.
                    form.remove();
                } else {
                    // Reset the main form for the next comment
                    form.reset();
                    if (form.querySelector(".char-count-current")) {
                        form.querySelector(".char-count-current").textContent = "0";
                    }
                }
            } else if (data.status === "pending_approval") {
                alert(data.message);
                form.reset();
                if (form.querySelector(".char-count-current")) {
                    form.querySelector(".char-count-current").textContent = "0";
                }
                if (isReply) {
                    form.remove();
                }
            } else {
                alert(data.message || T.i18nErrorGeneric);
            }
        } catch (error) {
            console.error("Error submitting comment:", error);
            alert(T.i18nErrorPost);
        } finally {
            submitBtn.disabled = false;
        }
    }

    // Main Form Submit Event
    if (mainForm) {
        mainForm.addEventListener("submit", function (e) {
            e.preventDefault();
            submitComment(mainForm, false);
        });
    }

    // Event Delegation for comment actions
    commentsWrapper.addEventListener("click", function (e) {
        // 1. Reply Button Clicked -> Insert inline form
        if (e.target.classList.contains("reply-trigger-btn") || e.target.closest(".reply-trigger-btn")) {
            const trigger = e.target.classList.contains("reply-trigger-btn") ? e.target : e.target.closest(".reply-trigger-btn");
            const commentCard = trigger.closest(".comment-card");
            const parentId = commentCard.getAttribute("data-comment-id");
            const formContainer = commentCard.querySelector(".comment-card__content > .reply-form-container");

            // Check if form is already open
            if (formContainer.querySelector(".reply-form")) {
                formContainer.querySelector(".reply-form").remove();
                return;
            }

            // Create inline reply form HTML
            const replyFormHtml = `
                <form class="comment-form reply-form">
                    <input type="hidden" name="page_id" value="${pageId}">
                    <input type="hidden" name="parent_id" value="${parentId}">
                    <div class="comment-form__input-wrapper">
                        <textarea 
                            name="body" 
                            class="comment-textarea" 
                            placeholder="${T.i18nReplyPlaceholder}" 
                            maxlength="3000" 
                            required
                        ></textarea>
                        <div class="comment-form__footer">
                            <span class="char-counter"><span class="char-count-current">0</span> / 3000</span>
                            <div class="reply-form__actions">
                                <button type="button" class="comment-action-btn cancel-reply-btn">${T.i18nReplyCancel}</button>
                                <button type="submit" class="submit-comment-btn">${T.i18nReplySubmit}</button>
                            </div>
                        </div>
                    </div>
                </form>
            `;

            formContainer.innerHTML = replyFormHtml;

            const replyForm = formContainer.querySelector(".reply-form");
            const replyTextarea = replyForm.querySelector(".comment-textarea");
            const replyCounter = replyForm.querySelector(".char-count-current");
            const replySubmit = replyForm.querySelector(".submit-comment-btn");

            initCharCounter(replyTextarea, replyCounter, replySubmit);
            replyTextarea.focus();

            // Reply Form submit event
            replyForm.addEventListener("submit", function (ev) {
                ev.preventDefault();
                submitComment(replyForm, true);
            });

            // Reply Form cancel button
            replyForm.querySelector(".cancel-reply-btn").addEventListener("click", function () {
                replyForm.remove();
            });
        }

        // 2. Delete Button Clicked
        if (e.target.classList.contains("delete-comment-btn") || e.target.closest(".delete-comment-btn")) {
            const btn = e.target.classList.contains("delete-comment-btn") ? e.target : e.target.closest(".delete-comment-btn");
            const url = btn.getAttribute("data-url");
            const commentCard = btn.closest(".comment-card");

            if (confirm(T.i18nConfirmDelete)) {
                fetch(url, {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": getCsrfToken()
                    }
                })
                .then(response => response.json())
                .then(data => {
                    if (data.status === "success") {
                        // Fade out and remove element from DOM
                        commentCard.style.transition = "opacity 0.3s ease, transform 0.3s ease";
                        commentCard.style.opacity = "0";
                        commentCard.style.transform = "translateY(5px)";
                        setTimeout(() => {
                            commentCard.remove();
                            // Check if comments list is empty
                            if (commentsList.children.length === 0 && emptyState) {
                                emptyState.style.display = "block";
                            }
                        }, 300);
                        updateCommentCount(-1);
                    } else {
                        alert(data.message);
                    }
                })
                .catch(err => {
                    console.error("Error deleting comment:", err);
                    alert(T.i18nErrorDelete);
                });
            }
        }

        // 3. Report/Flag Button Clicked
        if (e.target.classList.contains("report-comment-btn") || e.target.closest(".report-comment-btn")) {
            const btn = e.target.classList.contains("report-comment-btn") ? e.target : e.target.closest(".report-comment-btn");
            const url = btn.getAttribute("data-url");

            if (confirm(T.i18nConfirmReport)) {
                fetch(url, {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": getCsrfToken()
                    }
                })
                .then(response => response.json())
                .then(data => {
                    if (data.status === "success") {
                        btn.classList.add("reported");
                        btn.innerHTML = `<i class="fas fa-flag"></i> ${T.i18nReported}`;
                        btn.disabled = true;
                    } else {
                        alert(data.message);
                    }
                })
                .catch(err => {
                    console.error("Error reporting comment:", err);
                    alert(T.i18nErrorReport);
                });
            }
        }
    });

    // ── HASH ANCHOR ROUTING & GLOW HIGHLIGHTS ────────────────────────────────
    // Detects whether an inbound link points to a specific comment card
    const hash = window.location.hash;
    if (hash && hash.startsWith("#comment-")) {
        // Execute after a minimal delay to ensure content structures are fully rendered
        setTimeout(() => {
            const targetComment = document.querySelector(hash);
            if (targetComment) {
                // Height allowance offset to clear any sticky navigation header
                const headerOffset = 90; 
                const elementPosition = targetComment.getBoundingClientRect().top + window.scrollY;
                const offsetPosition = elementPosition - headerOffset;

                // Smoothly slide the browser viewport to the comment card focus area
                window.scrollTo({
                    top: offsetPosition,
                    behavior: "smooth"
                });

                // Attach the temporary soft glow contextual indicator classes
                targetComment.classList.add("comment-card--highlighted");

                // Cleanly purge the glow animation classes once execution finishes
                setTimeout(() => {
                    targetComment.classList.remove("comment-card--highlighted");
                }, 4000);
            }
        }, 400);
    }

});