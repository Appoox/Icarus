# Repository Guidelines & Operational Rules

## 🛑 STRICT VERSION CONTROL CONSTRAINTS (NON-NEGOTIABLE)

1. **NO GIT COMMIT OR PUSH**:
   - You are strictly forbidden from executing `git commit`, `git push`, or `git add`.
   - Do NOT attempt to create new git branches, rebase, or alter git history.

2. **FILE-BASED `.patch` PROTOCOL**:
   - Write ALL code changes—including modifications, brand-new files, and file deletions—directly to a file named `changes.patch` in the repository root directory using file-writing tools.
   - Do NOT output raw file contents or rely solely on chat markdown code blocks when making edits; the patch content must be written directly to `changes.patch` on disk.

3. **NEVER SUBMIT CHANGES TO GITHUB**:
   - Do NOT open pull requests, push branches, or otherwise submit anything to GitHub.
   - The user applies and tests the patch themselves (see rule 5 for how it reaches them).

4. **PATCHES ARE PROGRESSIVE WITHIN A CHAT, NOT CUMULATIVE**:
   - The first patch of a chat is a diff against the repository as checked out. Every later patch is a diff against the result of the previous patch: it holds only what changed since that patch, and the user applies it on top of the ones already applied.
   - Never regenerate a patch against the pristine repository, and never repeat changes an earlier patch already carried.
   - Keep the working tree edits from earlier turns in place, and keep a copy of the state the last patch produced (outside the repository — commits are not available for this), so the next `changes.patch` can be cut as the difference from it.

5. **DELIVER THE PATCH AS A DOWNLOADABLE FILE**:
   - Every time `changes.patch` changes, send it to the user as a downloadable file attachment in the same reply, using the session's file-sending tool (e.g. `SendUserFile`, as an attachment rather than an inline preview).
   - What is sent is that turn's progressive patch (rule 4), not a cumulative one. Say in the reply which patch it goes on top of; earlier files stay valid and are applied in the order they were sent.
   - Do NOT paste the patch into the chat. The reply carries a short summary of what changed; the file carries the diff.
   - If the session has no way to attach a file, say so and give the file's path instead of pasting the diff.
