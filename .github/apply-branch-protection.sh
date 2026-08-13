#!/usr/bin/env bash
# Apply the `main` branch ruleset and the repo's security settings.
#
#   ./.github/apply-branch-protection.sh
#
# Idempotent: re-running updates the existing ruleset rather than duplicating it.
#
# NOTE: rulesets require the repository to be PUBLIC (free) or the account to be
# on GitHub Pro. On a private repo on the Free plan the API returns 403
# "Upgrade to GitHub Pro or make this repository public to enable this feature."

set -euo pipefail

REPO="${REPO:-anubhabbehera/healing-hertz}"
RULESET_NAME="main protection"
# The CI job name that must pass before merge — must match .github/workflows/ci.yml
REQUIRED_CHECK="lint & test"

read -r -d '' PAYLOAD <<JSON || true
{
  "name": "$RULESET_NAME",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
  ],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "require_code_owner_review": true,
        "dismiss_stale_reviews_on_push": true,
        "require_last_push_approval": true,
        "required_review_thread_resolution": true,
        "allowed_merge_methods": ["squash", "rebase", "merge"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "do_not_enforce_on_create": false,
        "required_status_checks": [ { "context": "$REQUIRED_CHECK" } ]
      }
    }
  ]
}
JSON

existing=$(gh api "repos/$REPO/rulesets" --jq \
  ".[] | select(.name == \"$RULESET_NAME\") | .id" 2>/dev/null || true)

if [ -n "$existing" ]; then
  echo "Updating existing ruleset $existing…"
  printf '%s' "$PAYLOAD" | gh api -X PUT "repos/$REPO/rulesets/$existing" --input - --jq '.name + " → " + .enforcement'
else
  echo "Creating ruleset…"
  printf '%s' "$PAYLOAD" | gh api -X POST "repos/$REPO/rulesets" --input - --jq '.name + " → " + .enforcement'
fi

echo "Enabling security features…"
gh api -X PUT "repos/$REPO/vulnerability-alerts" --silent && echo "  Dependabot alerts ✓"
gh api -X PUT "repos/$REPO/automated-security-fixes" --silent && echo "  Dependabot security updates ✓"

# Secret scanning and push protection are free on public repositories; on a
# private repo they need GitHub Advanced Security and this call will fail.
gh api -X PATCH "repos/$REPO" --input - >/dev/null <<'JSON' 2>/dev/null \
  && echo "  Secret scanning + push protection ✓" \
  || echo "  Secret scanning skipped (needs a public repo, or GHAS on private)"
{ "security_and_analysis": {
    "secret_scanning": { "status": "enabled" },
    "secret_scanning_push_protection": { "status": "enabled" }
} }
JSON

gh api -X PATCH "repos/$REPO" \
  -F delete_branch_on_merge=true -F allow_update_branch=true --silent \
  && echo "  Merge hygiene ✓"

# A workflow token that can't write can't be turned into a supply-chain foothold.
gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false --silent \
  && echo "  GITHUB_TOKEN read-only ✓"

echo
echo "Done. Verify at: https://github.com/$REPO/settings/rules"
echo
echo "Two settings still need the web UI (no REST endpoint):"
echo "  • Settings → Actions → 'Require approval for all external contributors'"
echo "    — stops drive-by PRs from running workflows on your runners."
echo "  • Security → Code scanning → set up CodeQL (default setup)."
