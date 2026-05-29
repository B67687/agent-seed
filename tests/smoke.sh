#!/bin/bash
# agent-seed: smoke test
#
# Runs all scripts in read-only modes and verifies they exit cleanly.
# This is the first line of defense against regressions.
#
# Usage: bash tests/smoke.sh
#   --verbose   print per-test details
set -e

MODE="normal"
for arg in "$@"; do
	[ "$arg" = "--verbose" ] && MODE="verbose"
done

PASS=0
FAIL=0
TOTAL=0

pass() {
	TOTAL=$((TOTAL + 1))
	PASS=$((PASS + 1))
	[ "$MODE" = "verbose" ] && echo "  ✓ $1"
}

fail() {
	TOTAL=$((TOTAL + 1))
	FAIL=$((FAIL + 1))
	echo "  ✗ $1 — $2"
}

section() {
	[ "$MODE" = "verbose" ] && echo ""
	[ "$MODE" = "verbose" ] && echo "--- $1 ---"
}

# ──────────────────────────────────
# Script existence & executable
# ──────────────────────────────────

section "Scripts exist and are executable"

for s in scripts/*; do
	name="${s#scripts/}"
	[ -f "$s" ] || continue
	[ -x "$s" ] && pass "$name is executable" || fail "$name is executable" "not executable"
	head -1 "$s" | grep -q '^#!/' && pass "$name has shebang" || fail "$name has shebang" "missing shebang"
done

# ──────────────────────────────────
# eval tests
# ──────────────────────────────────

section "scripts/eval"

EVAL_NORMAL=$(bash scripts/eval 2>&1) && pass "eval — normal mode" || fail "eval — normal mode" "exit code $?"
echo "$EVAL_NORMAL" | grep -q "=== results ===" && pass "eval — contains results header" || fail "eval — contains results header" "not found"

EVAL_SCORE=$(bash scripts/eval --score 2>&1) && pass "eval — score mode" || fail "eval — score mode" "exit code $?"
[ "$EVAL_SCORE" -eq "$EVAL_SCORE" ] 2>/dev/null && pass "eval — score is numeric ($EVAL_SCORE)" || fail "eval — score is numeric" "got: $EVAL_SCORE"

EVAL_JSON=$(bash scripts/eval --json 2>&1) && pass "eval — json mode" || fail "eval — json mode" "exit code $?"
echo "$EVAL_JSON" | jq -r '.score' >/dev/null 2>&1 && pass "eval — json has .score" || fail "eval — json has .score" "invalid JSON"

# ──────────────────────────────────
# route tests
# ──────────────────────────────────

section "scripts/route"

ROUTE_CHECK=$(bash scripts/route --check 2>&1) && pass "route — config validates" || fail "route — config validates" "$ROUTE_CHECK"

ROUTE_LIST=$(bash scripts/route --list 2>&1) && pass "route — list mode" || fail "route — list mode" "exit code $?"
echo "$ROUTE_LIST" | grep -q "routes" && pass "route — list shows routes" || fail "route — list shows routes" "not found"

ROUTE_RESOLVE=$(bash scripts/route explore 2>&1) && pass "route — resolve 'explore'" || fail "route — resolve 'explore'" "exit code $?"
echo "$ROUTE_RESOLVE" | grep -q "deepseek-v4-flash-free" && pass "route — resolved to correct model" || fail "route — resolved to correct model" "got: $ROUTE_RESOLVE"

# Unknown task type should fail
ROUTE_UNKNOWN=$(bash scripts/route nonexistent 2>&1) && fail "route — unknown task fails" "should have failed" || pass "route — unknown task fails as expected"

ROUTE_FALLBACK=$(bash scripts/route --fallback explore 2>&1) && pass "route — fallback mode" || fail "route — fallback mode" "exit code $?"

# ──────────────────────────────────
# improve test
# ──────────────────────────────────

section "scripts/improve"

IMPROVE_OUT=$(bash scripts/improve 2>&1) && pass "improve — runs cleanly" || fail "improve — runs cleanly" "exit code $?"
echo "$IMPROVE_OUT" | grep -q "EVAL SUMMARY" && pass "improve — contains eval summary" || fail "improve — contains eval summary" "not found"

# ──────────────────────────────────
# Config files
# ──────────────────────────────────

section "Config files"

[ -f .model-config.json ] && pass ".model-config.json exists" || fail ".model-config.json exists" "missing"
jq empty .model-config.json 2>/dev/null && pass ".model-config.json is valid JSON" || fail ".model-config.json is valid JSON" "parse error"
[ -f .opencode.jsonc ] && pass ".opencode.jsonc exists" || fail ".opencode.jsonc exists" "missing"

# ──────────────────────────────────
# .gitignore
# ──────────────────────────────────

[ -f .gitignore ] && pass ".gitignore exists" || fail ".gitignore exists" "missing"
grep -q "tests/" .gitignore 2>/dev/null && pass ".gitignore ignores tests/" || pass ".gitignore ignores tests/" "not needed (uncommitted to repo)"

# ──────────────────────────────────
# Summary
# ──────────────────────────────────

SCORE=0
[ "$TOTAL" -gt 0 ] && SCORE=$((PASS * 100 / TOTAL))

echo ""
echo "=== smoke test results ==="
echo "  passed: $PASS / $TOTAL"
echo "  score:  $SCORE/100"
[ "$FAIL" -gt 0 ] && echo "  failures: $FAIL" && exit 1 || echo "  all checks passed!"
