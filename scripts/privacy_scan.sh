#!/usr/bin/env bash
# Privacy scan for this public repo (see AGENTS.md). Exits non-zero on any
# match in tracked files. Patterns live in scripts/privacy_patterns.txt so
# the pattern strings themselves never have to appear in other tracked files.
set -euo pipefail
cd "$(dirname "$0")/.."

PATTERNS_FILE="scripts/privacy_patterns.txt"
status=0

# 1) Tracked files against every pattern.
while IFS= read -r pattern; do
  [[ -z "$pattern" || "$pattern" == \#* ]] && continue
  if matches=$(git grep -nEIe "$pattern" -- . 2>/dev/null | grep -v -F -x -f <(printf '%s\n' "$pattern") | grep -v '^scripts/privacy_patterns.txt:' || true); then
    [[ -n "$matches" ]] && { echo "MATCH for pattern: $pattern"; echo "$matches"; status=1; }
  fi
done < "$PATTERNS_FILE"

# 2) Real (non-locally-administered) MAC addresses anywhere in tracked files.
while IFS= read -r mac; do
  octet=$(printf '%s' "${mac%%:*}" | tr 'a-f' 'A-F')
  case "$octet" in
    0[2-6AEF]|1[2-6AEF]|2[2-6AEF]|3[2-6AEF]|4[2-6AEF]|5[2-6AEF]|6[2-6AEF]|7[2-6AEF]|8[2-6AEF]|9[2-6AEF]|A[2-6AEF]|B[2-6AEF]|C[2-6AEF]|D[2-6AEF]|E[2-6AEF]|F[2-6AEF]) ;;
    *) echo "REAL MAC (administered bit clear): $mac"; status=1 ;;
  esac
done < <(git grep -hoEIe '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' -- . | sort -u)

# 3) Secrets / local files accidentally tracked.
if git ls-files | grep -qE '(^|/)\.env$|cookie.*\.txt$|^tmp/|unifi-backups/'; then
  echo "TRACKED secret-like file:"; git ls-files | grep -E '(^|/)\.env$|cookie.*\.txt$|^tmp/|unifi-backups/'
  status=1
fi

if [[ $status -eq 0 ]]; then
  echo "privacy scan: PASS (0 matches)"
else
  echo "privacy scan: FAIL" >&2
fi
exit $status
