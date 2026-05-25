document.addEventListener("DOMContentLoaded", function () {
    const commentsWrapper = document.getElementById("comments-wrapper");
    if (!commentsWrapper) return;

    const pageId = commentsWrapper.getAttribute("data-page-id");
    const mainForm = document.getElementById("main-comment-form");
    const commentsList = document.getElementById("comments-list-container");
    const emptyState = document.getElementById("comments-empty-message");
    const countBadge = document.getElementById("comment-count-badge");

    // Helper: get CSRF token
    function getCsrfToken() {
        return document.querySelector('[name=csrfmiddlewaretoken]').value;
    }

    // Helper: update comment count badge
    function updateCommentCount(offset) {
        if (countBadge) {
            let current = parseInt(countBadge.textContent) || 0;
            countBadge.textContent = Math.max(0, current + offset);
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
                // If comment list has an empty state, remove it
                if (emptyState) {
                    emptyState.style.display = "none";
                }

                // If reply, insert under parent comment's replies-list
                if (isReply && data.parent_id) {
                    const parentCard = document.getElementById(`comment-${data.parent_id}`);
                    if (parentCard) {
                        const parentRepliesList = parentCard.querySelector(".replies-list");
                        parentRepliesList.insertAdjacentHTML("beforeend", data.html);
                    }
                    // Remove the reply form
                    form.remove();
                } else {
                    // Prepend/append top-level comment (oldest first, so we append at the bottom)
                    commentsList.insertAdjacentHTML("beforeend", data.html);
                    form.reset();
                    form.querySelector(".char-count-current").textContent = "0";
                }

                updateCommentCount(1);
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
                alert(data.message || "Something went wrong. Please try again.");
            }
        } catch (error) {
            console.error("Error submitting comment:", error);
            alert("Error posting comment. Please try again.");
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
                            placeholder="മറുപടി രേഖപ്പെടുത്തുക..." 
                            maxlength="3000" 
                            required
                        ></textarea>
                        <div class="comment-form__footer">
                            <span class="char-counter"><span class="char-count-current">0</span> / 3000</span>
                            <div class="reply-form__actions">
                                <button type="button" class="comment-action-btn cancel-reply-btn">റദ്ദാക്കുക</button>
                                <button type="submit" class="submit-comment-btn">മറുപടി നൽകുക</button>
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

            if (confirm("ഈ അഭിപ്രായം ഒഴിവാക്കണോ?")) {
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
                    alert("Error removing comment.");
                });
            }
        }

        // 3. Report/Flag Button Clicked
        if (e.target.classList.contains("report-comment-btn") || e.target.closest(".report-comment-btn")) {
            const btn = e.target.classList.contains("report-comment-btn") ? e.target : e.target.closest(".report-comment-btn");
            const url = btn.getAttribute("data-url");

            if (confirm("ഈ അഭിപ്രായം റിപ്പോർട്ട് ചെയ്യണോ?")) {
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
                        btn.innerHTML = `<i class="fas fa-flag"></i> റിപ്പോർട്ട് ചെയ്തു`;
                        btn.disabled = true;
                    } else {
                        alert(data.message);
                    }
                })
                .catch(err => {
                    console.error("Error reporting comment:", err);
                    alert("Error reporting comment.");
                });
            }
        }
    });
});
