#!/usr/bin/env bash
# quanta automatic update — run hourly by quanta-update.timer (bootstrap.sh --auto-update on).
#
# Pull-based: the server looks at GitHub; nothing connects to the server.
#   1. fetch the installed branch; nothing new → done
#   2. deploy the new commit only if every GitHub check run on it finished green
#      (pending → try again next hour; red → skip that commit for good)
#   3. check out the new commit and run *its* installer unattended (it waits for the status
#      page and the services); if that fails, check out the previous commit and re-install it
#   4. record the outcome in <data>/update.json (shown on the status page)
#
# Environment (tests/mirrors): QUANTA_GITHUB_API (default https://api.github.com),
# QUANTA_UPDATE_SKIP_CI=1 (root-only escape hatch: deploy without the CI gate).
# A GitHub token for a private repository is read from /etc/quanta/secrets/github_token.
set -Eeuo pipefail

main() {
  local install_dir=/opt/quanta env_file=/etc/quanta/compose.env
  local lock=/run/quanta-bootstrap.lock log=/var/log/quanta-update.log
  [[ $EUID -eq 0 ]] || { echo "root gerekli" >&2; exit 1; }
  [[ -r "$env_file" ]] || { echo "kurulum bulunamadı ($env_file)" >&2; exit 1; }
  local data repo branch secrets
  data="$(sed -n 's/^QUANTA_DATA=//p' "$env_file" | tail -n1)"
  repo="$(sed -n 's/^QUANTA_REPO=//p' "$env_file" | tail -n1)"
  branch="$(sed -n 's/^QUANTA_BRANCH=//p' "$env_file" | tail -n1)"
  secrets="$(sed -n 's/^QUANTA_SECRETS=//p' "$env_file" | tail -n1)"
  branch="${branch:-$(git -C "$install_dir" rev-parse --abbrev-ref HEAD)}"
  repo="${repo:-coniiamca/Bot2-de-ifre}"
  local state="$data/update.json"

  exec 9>"$lock"
  if ! flock -n 9; then
    echo "an install or update is already running; next check in an hour"
    return 0
  fi
  exec > >(tee -a "$log") 2>&1
  echo "== quanta auto-update $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

  local failed=""
  if [[ -r "$state" ]]; then
    failed="$(sed -n 's/.*"failed_sha": "\([0-9a-f]*\)".*/\1/p' "$state")"
  fi

  git -C "$install_dir" fetch -q origin "$branch"
  local cur new
  cur="$(git -C "$install_dir" rev-parse HEAD)"
  new="$(git -C "$install_dir" rev-parse "origin/$branch")"
  if [[ "$cur" == "$new" ]]; then
    write_state "$state" "$branch" "$cur" "$new" up_to_date "$failed"
    echo "up to date: ${cur:0:7}"
    return 0
  fi
  if [[ "$new" == "$failed" ]]; then
    write_state "$state" "$branch" "$cur" "$new" skipped_failed "$failed"
    echo "skipping ${new:0:7}: it failed before; waiting for a newer commit"
    return 0
  fi

  local ci=success
  if [[ -z "${QUANTA_UPDATE_SKIP_CI:-}" ]]; then
    ci="$(ci_state "$repo" "$new" "$secrets")"
  fi
  case "$ci" in
    success) ;;
    pending)
      write_state "$state" "$branch" "$cur" "$new" waiting_ci "$failed"
      echo "${new:0:7}: CI still running (or not started); next check in an hour"
      return 0
      ;;
    failure)
      write_state "$state" "$branch" "$cur" "$new" ci_failed "$new"
      echo "${new:0:7}: CI failed; not deploying it"
      return 0
      ;;
    *)
      write_state "$state" "$branch" "$cur" "$new" ci_unknown "$failed"
      echo "${new:0:7}: cannot read CI results from GitHub (private repo without token?); not deploying"
      return 0
      ;;
  esac

  echo "deploying ${cur:0:7} → ${new:0:7}"
  local url
  url="$(git -C "$install_dir" remote get-url origin)"
  git -C "$install_dir" checkout -q -B "$branch" "$new"
  if QUANTA_LOCK_HELD=1 QUANTA_PREV_HEAD="$cur" QUANTA_REPO_URL="$url" QUANTA_BRANCH="$branch" \
    bash "$install_dir/deploy/bootstrap.sh" --unattended --ref "$new"; then
    write_state "$state" "$branch" "$new" "$new" updated "$failed"
    echo "updated to ${new:0:7}"
    return 0
  fi

  echo "install of ${new:0:7} failed; rolling back to ${cur:0:7}"
  git -C "$install_dir" checkout -q -B "$branch" "$cur"
  if QUANTA_LOCK_HELD=1 QUANTA_PREV_HEAD="$new" QUANTA_REPO_URL="$url" QUANTA_BRANCH="$branch" \
    bash "$install_dir/deploy/bootstrap.sh" --unattended --ref "$cur"; then
    write_state "$state" "$branch" "$cur" "$new" failed_rolled_back "$new"
    echo "rolled back to ${cur:0:7}"
  else
    write_state "$state" "$branch" "$cur" "$new" rollback_failed "$new"
    echo "ROLLBACK FAILED — run: sudo bash $install_dir/deploy/bootstrap.sh"
    return 1
  fi
}

# success | pending | failure | unknown for the GitHub check runs of a commit
ci_state() {
  local repo="$1" sha="$2" secrets="$3" body
  local api="${QUANTA_GITHUB_API:-https://api.github.com}"
  local args=(-fsS --max-time 30 -H "Accept: application/vnd.github+json")
  if [[ -n "$secrets" && -r "$secrets/github_token" ]]; then
    args+=(-H "Authorization: Bearer $(tr -d '[:space:]' <"$secrets/github_token")")
  fi
  body="$(curl "${args[@]}" "$api/repos/$repo/commits/$sha/check-runs?per_page=100" 2>/dev/null)" \
    || { echo unknown; return 0; }
  python3 -c '
import json, sys
try:
    runs = json.load(sys.stdin).get("check_runs", [])
except ValueError:
    print("unknown"); sys.exit()
if not runs or any(r.get("status") != "completed" for r in runs):
    print("pending")
elif all(r.get("conclusion") in ("success", "skipped", "neutral") for r in runs):
    print("success")
else:
    print("failure")
' <<<"$body"
}

write_state() {
  local file="$1" branch="$2" current="$3" latest="$4" result="$5" failed="$6" tmp
  [[ -d "$(dirname "$file")" ]] || return 0
  tmp="$(mktemp "$(dirname "$file")/.update.json.XXXXXX")"
  printf '{"checked_at": "%s", "branch": "%s", "current": "%s", "latest": "%s", "result": "%s", "failed_sha": "%s", "auto_update": true}\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$branch" "$current" "$latest" "$result" "$failed" >"$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$file"
}

main "$@"
exit $?
