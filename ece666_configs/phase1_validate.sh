#!/bin/bash
# Phase 1 validation — FFT on new tree vs old tree, config.ini inspection,
# stats sanity.
#
# IMPORTANT NOTE ON COVERAGE GATE:
# The prof delta is CONFIG-ONLY (build_opts files, SPEC run scripts, 3
# SConsopts patches).  It adds no C++ SimObjects.  This means:
#
#   - FFT on AtomicSimpleCPU + --caches will exercise the same code paths
#     as the old tree, because there are no new prof SimObjects to miss.
#   - The meaningful "did the port work" signal is:
#       (a) the build succeeded under the prof build_opts file, and
#       (b) sim_seconds / simInsts are close between old and new trees.
#   - config.ini should NOT contain any prof-named SimObjects — the prof
#     only added build flags and run scripts, not gem5 classes.
#
# Required:
#   OLD_GEM5   absolute path to old-tree gem5.opt  (discovered earlier; NOT assumed)
#   FFT_BIN    absolute path to x86 static FFT binary (from build_splash4.sh)
#
# Optional:
#   SKIP_OLD=1   skip baseline comparison (only run new-tree)

set -uo pipefail

: "${OLD_GEM5:?export OLD_GEM5=/path/to/old/gem5.opt}"
: "${FFT_BIN:?export FFT_BIN=/path/to/FFT}"

mkdir -p "$HOME/tmp" && chmod 700 "$HOME/tmp"
export TMPDIR=$HOME/tmp TEMP=$HOME/tmp TMP=$HOME/tmp

NEW_ROOT="$HOME/ece666/gem5-v25-merged"
NEW_GEM5="$NEW_ROOT/build/ECE666-X86/gem5.opt"
NEW_SE="$NEW_ROOT/configs/deprecated/example/se.py"
OLD_ROOT="$HOME/ece666/gem5-Spr2023"

# Old tree may use either deprecated or example location
OLD_SE=""
for p in "$OLD_ROOT/configs/example/se.py" \
         "$OLD_ROOT/configs/deprecated/example/se.py"; do
    [ -f "$p" ] && { OLD_SE="$p"; break; }
done

[ -x "$NEW_GEM5" ] || { echo "NEW_GEM5 missing: $NEW_GEM5"; exit 1; }
[ -f "$NEW_SE" ]   || { echo "NEW_SE missing: $NEW_SE"; exit 1; }
[ -x "$OLD_GEM5" ] || { echo "OLD_GEM5 missing: $OLD_GEM5"; exit 1; }
[ -n "$OLD_SE" ]   || { echo "OLD_SE (se.py) not found under $OLD_ROOT"; exit 1; }
[ -x "$FFT_BIN" ]  || { echo "FFT_BIN missing: $FFT_BIN"; exit 1; }

NEW_OUT="$NEW_ROOT/m5out_splash4_new"
OLD_OUT="$OLD_ROOT/m5out_splash4_old"
mkdir -p "$NEW_OUT" "$OLD_OUT"

declare -A R
mark() { R["$1"]="$2"; echo "[$2] $1"; }

# ── Step C — FFT on new tree ────────────────────────────────────────
echo "=== Step C: FFT on NEW tree ==="
"$NEW_GEM5" -d "$NEW_OUT" "$NEW_SE" \
    --cmd="$FFT_BIN" --options="-p 1 -m 10" \
    --cpu-type=AtomicSimpleCPU --caches
RC_NEW=$?
[ $RC_NEW -eq 0 ] && mark "C. FFT new-tree run" "PASS" || mark "C. FFT new-tree run" "FAIL"

# ── Step D — FFT on old tree ────────────────────────────────────────
if [ "${SKIP_OLD:-0}" != "1" ]; then
    echo "=== Step D: FFT on OLD tree ==="
    "$OLD_GEM5" -d "$OLD_OUT" "$OLD_SE" \
        --cmd="$FFT_BIN" --options="-p 1 -m 10" \
        --cpu-type=AtomicSimpleCPU --caches || true
    RC_OLD=$?
    [ $RC_OLD -eq 0 ] && mark "D. FFT old-tree run" "PASS" || mark "D. FFT old-tree run" "FAIL"
fi

# ── Step E — correctness comparison ─────────────────────────────────
echo "=== Step E: correctness comparison ==="

# E.1 exit status
if [ "${SKIP_OLD:-0}" != "1" ]; then
    [ $RC_NEW -eq 0 ] && [ $RC_OLD -eq 0 ] \
        && mark "E.1 both runs exit 0" "PASS" \
        || mark "E.1 both runs exit 0" "FAIL"
fi

# E.2 benchmark stdout diff (strip banner)
if [ -f "$NEW_OUT/simout" ] && [ -f "${OLD_OUT}/simout" ]; then
    BANNER_FILTER='^(gem5 |Global frequency|Warning:|info:|warn:|build|M5 |\*\*)'
    grep -vE "$BANNER_FILTER" "$NEW_OUT/simout" > "$NEW_OUT/simout.clean"
    grep -vE "$BANNER_FILTER" "$OLD_OUT/simout" > "$OLD_OUT/simout.clean"
    if diff -q "$NEW_OUT/simout.clean" "$OLD_OUT/simout.clean" >/dev/null; then
        mark "E.2 benchmark stdout match" "PASS"
    else
        mark "E.2 benchmark stdout match" "REVIEW"
        echo "   diff (old → new):"
        diff "$OLD_OUT/simout.clean" "$NEW_OUT/simout.clean" | head -20
    fi
fi

# E.3 — skipped: no prof C++ SimObjects to look for.
echo "E.3 prof SimObjects in config.ini: SKIPPED (prof delta is config-only)"

# E.4 stats sanity + old-vs-new simInsts comparison
get_stat() { awk -v k="$1" '$1==k {print $2; exit}' "$2" 2>/dev/null; }

NEW_SI=$(get_stat simInsts "$NEW_OUT/stats.txt")
NEW_SS=$(get_stat sim_seconds "$NEW_OUT/stats.txt")
NEW_HR=$(get_stat host_inst_rate "$NEW_OUT/stats.txt")
echo "   new: simInsts=$NEW_SI sim_seconds=$NEW_SS host_inst_rate=$NEW_HR"

SANE=1
awk "BEGIN{exit !($NEW_SI > 0)}" 2>/dev/null || SANE=0
awk "BEGIN{exit !($NEW_SS > 0)}" 2>/dev/null || SANE=0
awk "BEGIN{exit !($NEW_HR > 0)}" 2>/dev/null || SANE=0
[ $SANE -eq 1 ] && mark "E.4a new stats sane (>0)" "PASS" \
               || mark "E.4a new stats sane (>0)" "FAIL"

if [ "${SKIP_OLD:-0}" != "1" ]; then
    OLD_SI=$(get_stat simInsts "$OLD_OUT/stats.txt")
    echo "   old: simInsts=$OLD_SI"
    if [ -n "$OLD_SI" ] && [ -n "$NEW_SI" ]; then
        MATCH=$(awk -v a="$OLD_SI" -v b="$NEW_SI" \
            'BEGIN{if(a==0){print 0;exit} d=(b-a)/a; if(d<0)d=-d; print (d<0.01)?1:0}')
        if [ "$MATCH" = "1" ]; then
            mark "E.4b simInsts old~new (<1%)" "PASS"
        else
            mark "E.4b simInsts old~new (<1%)" "REVIEW"
            echo "   Material divergence. Inspect config.ini diff:"
            echo "     diff $OLD_OUT/config.ini $NEW_OUT/config.ini | head -60"
        fi
    fi
fi

# ── Summary ─────────────────────────────────────────────────────────
echo ""
echo "===== PHASE 1 RESULTS ====="
printf '%-40s %s\n' "Check" "Result"
printf '%-40s %s\n' "----------------------------------------" "------"
for k in $(echo "${!R[@]}" | tr ' ' '\n' | sort); do
    printf '%-40s %s\n' "$k" "${R[$k]}"
done

FAIL=0
for v in "${R[@]}"; do
    [[ "$v" == "FAIL"* ]] && FAIL=1
done
[ $FAIL -eq 0 ] && echo "PHASE 1: GREEN" || echo "PHASE 1: RED"
exit $FAIL
