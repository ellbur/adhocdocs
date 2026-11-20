#!/bin/sh
# Keeps the sample matter's work product (memos, pages and their citation files) on this
# session's branch when Claude stops, so it can be picked up from outside the session.
# The matter's documents and indexes stay out (matter/ is ignored).
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
[ -d matter/workspace ] || exit 0
files=$(cd matter/workspace && find . -maxdepth 2 -type f \( -name '*.md' -o -name '*.jsonl' -o -name '*.py' \) \
        ! -path './.adhoc/*' ! -path './files/*' | sed 's|^\./|matter/workspace/|')
[ -n "$files" ] || exit 0
git add -f -- $files >/dev/null 2>&1 || exit 0
git diff --cached --quiet && exit 0
git commit -q -m "Sync the workspace" >/dev/null 2>&1 || exit 0
timeout 60 git push -q origin HEAD >/dev/null 2>&1
exit 0
