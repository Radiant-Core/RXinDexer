#!/usr/bin/env bash
#
# H2 — purge committed secrets from RXinDexer git history.
#
# WHAT THIS REMOVES FROM ALL HISTORY:
#   1. The mainnet RPC password literal  __ROTATED_SEE_ENV__  (introduced in
#      commit c972bec, present in docker-compose.yaml from then on).
#   2. The committed TLS private key + cert  electrumdb/server.key,
#      electrumdb/server.crt  (added in 61d1054, removed in fc76244 — but still
#      in history).
#
# ⚠️  THIS REWRITES ALL COMMIT HASHES and requires a FORCE-PUSH to the shared
#     Radiant-Core/RXinDexer repo.  Coordinate first: every collaborator must
#     re-clone afterward, and open PRs/branches will need to be rebased/recreated.
#
# ⚠️  ROTATION IS THE REAL FIX.  These secrets have been public; purging history
#     does NOT un-leak them.  You MUST also:
#       - rotate the node's rpcpassword (radiant.conf) + restart radiantd,
#       - regenerate the TLS server keypair (the old key is compromised).
#     See the runbook in the chat for those steps.
#
# RUN THIS ON A FRESH MIRROR CLONE, verify, then force-push:
#     git clone --mirror https://github.com/Radiant-Core/RXinDexer.git rxindexer-purge.git
#     cd rxindexer-purge.git
#     bash /path/to/purge-history-secrets.sh
#     # inspect, then:  git push --force --mirror origin
#
set -euo pipefail

# 1. Ensure git-filter-repo is available (preferred; safe + fast).
if ! command -v git-filter-repo >/dev/null 2>&1 && ! python3 -c 'import git_filter_repo' 2>/dev/null; then
  echo ">> Installing git-filter-repo (pip)…"
  pip install --user git-filter-repo
fi
FILTER_REPO=(git filter-repo)
command -v git-filter-repo >/dev/null 2>&1 || FILTER_REPO=(python3 -m git_filter_repo)

# 2. Build the replace-text rules (password -> placeholder).
REPL="$(mktemp)"
trap 'rm -f "$REPL"' EXIT
cat > "$REPL" <<'EOF'
__ROTATED_SEE_ENV__==>__ROTATED_SEE_ENV__
EOF

echo ">> Stripping the TLS key/cert blobs from all history…"
"${FILTER_REPO[@]}" --force \
  --path electrumdb/server.key \
  --path electrumdb/server.crt \
  --invert-paths

echo ">> Redacting the RPC password literal from all history…"
"${FILTER_REPO[@]}" --force --replace-text "$REPL"

echo ""
echo ">> DONE rewriting history. VERIFY before pushing:"
echo "     git log --oneline -S 'RxS3cur3P@ssw0rd' --all        # expect: no results"
echo "     git log --oneline --all -- electrumdb/server.key      # expect: no results"
echo "     git grep -n 'RxS3cur3P' \$(git rev-list --all) | head # expect: empty"
echo ""
echo ">> Then push (DESTRUCTIVE, coordinate first):"
echo "     git push --force --mirror origin"
echo ""
echo ">> AFTER pushing: rotate the node rpcpassword + regenerate TLS keys (they are still compromised)."
