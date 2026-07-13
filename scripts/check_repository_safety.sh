#!/bin/sh

set -eu

mode=worktree
if [ "${1:-}" = "--staged" ]; then
  mode=staged
elif [ "$#" -ne 0 ]; then
  echo "Usage: $0 [--staged]" >&2
  exit 2
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Safety check must run inside a Git worktree." >&2
  exit 2
fi

failures=0
file_list=$(mktemp "${TMPDIR:-/tmp}/worldcup-safety.XXXXXX")
trap 'rm -f "$file_list"' EXIT HUP INT TERM

report_failure() {
  category=$1
  path=$2
  printf 'ERROR: %s: %s\n' "$category" "$path"
  failures=$((failures + 1))
}

report_warning() {
  category=$1
  path=$2
  printf 'WARNING: %s: %s\n' "$category" "$path"
}

check_path() {
  path=$1
  case "$path" in
    .env|*/.env)
      report_failure "prohibited path" "$path"
      ;;
    *.pem|*.key|*.p12|*.pfx)
      report_failure "private credential file" "$path"
      ;;
  esac
}

content_category() {
  if grep -E -q -- '-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----'; then
    printf '%s\n' "private key content"
  elif grep -E -q -- '(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})'; then
    printf '%s\n' "GitHub token content"
  elif grep -E -q -- 'sk-(proj-)?[A-Za-z0-9_-]{20,}'; then
    printf '%s\n' "OpenAI key content"
  elif grep -E -q -- 'AKIA[0-9A-Z]{16}'; then
    printf '%s\n' "AWS access key content"
  elif grep -E -i -q -- "(api[_-]?key|access[_-]?token|session[_-]?token|client[_-]?secret|password)[[:space:]]*[=:][[:space:]]*[\"']?[A-Za-z0-9_./+=-]{16,}"; then
    printf '%s\n' "assigned credential-like content"
  fi
}

check_worktree_file() {
  path=$1
  check_path "$path"
  [ -f "$path" ] || return 0
  case "$path" in
    .env.example|*/.env.example)
      return 0
      ;;
  esac
  category=$(content_category <"$path" || true)
  if [ -n "$category" ]; then
    report_failure "$category" "$path"
  fi
}

check_staged_file() {
  path=$1
  check_path "$path"
  case "$path" in
    .env.example|*/.env.example)
      return 0
      ;;
  esac
  category=$(git show ":$path" 2>/dev/null | content_category || true)
  if [ -n "$category" ]; then
    report_failure "$category" "$path"
  fi
}

if [ "$mode" = "staged" ]; then
  git diff --cached --name-only --diff-filter=ACMR >"$file_list"
  while IFS= read -r path; do
    [ -n "$path" ] && check_staged_file "$path"
  done <"$file_list"
else
  if git ls-files --error-unmatch .env >/dev/null 2>&1; then
    report_failure "prohibited path" ".env"
  fi
  git ls-files -co --exclude-standard >"$file_list"
  while IFS= read -r path; do
    [ -n "$path" ] && check_worktree_file "$path"
  done <"$file_list"
fi

find . \
  -path './.git' -prune -o \
  -path './.venv' -prune -o \
  -type f -size +50M -print >"$file_list"
while IFS= read -r path; do
    path=${path#./}
    if [ -f "$path" ] && [ "$(wc -c <"$path")" -gt $((95 * 1024 * 1024)) ]; then
      report_failure "file exceeds 95 MiB safety limit" "$path"
    else
      report_warning "large file warning (over 50 MiB)" "$path"
    fi
done <"$file_list"

if [ -f data/worldcup.sqlite3 ] && command -v sqlite3 >/dev/null 2>&1; then
  integrity=$(sqlite3 data/worldcup.sqlite3 'PRAGMA integrity_check;' 2>/dev/null || true)
  if [ "$integrity" != "ok" ]; then
    report_failure "SQLite integrity check failed" "data/worldcup.sqlite3"
  fi
  sensitive_columns=$(sqlite3 data/worldcup.sqlite3 \
    "SELECT COUNT(*) FROM sqlite_schema AS m JOIN pragma_table_info(m.name) AS p WHERE m.type='table' AND (lower(p.name) LIKE '%password%' OR lower(p.name) LIKE '%token%' OR lower(p.name) LIKE '%secret%' OR lower(p.name) LIKE '%api_key%' OR lower(p.name) LIKE '%email%' OR lower(p.name) LIKE '%phone%');" \
    2>/dev/null || printf '1')
  if [ "$sensitive_columns" != "0" ]; then
    report_failure "sensitive-looking SQLite column name" "data/worldcup.sqlite3"
  fi
fi

if [ "$failures" -ne 0 ]; then
  printf 'Repository safety check failed with %s finding(s).\n' "$failures"
  exit 1
fi

echo "Repository safety check passed."
