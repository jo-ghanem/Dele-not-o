#!/bin/bash
# One-shot deploy: clone gem5 v25.1.0.0, apply professor delta, build X86,
# smoke-test with hello-world SE run.
#
# Prereqs on qstruct: git, scons, python3, a C++ compiler (g++).
# Does NOT require HDF5 / protobuf / hdf5-cpp — prof patches disable those.
#
# Usage:
#   scp -r port/ jghanem@qstruct:~/ece666/port/
#   ssh jghanem@qstruct 'bash ~/ece666/port/deploy_to_qstruct.sh'
#
# All paths assume this script lives in PORT_DIR alongside patches/,
# build_opts/, configs_spec/.

set -euo pipefail

# ── Resolve own location ─────────────────────────────────────────────
PORT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Environment: avoid /tmp ──────────────────────────────────────────
mkdir -p "$HOME/tmp"
chmod 700 "$HOME/tmp"
export TMPDIR="$HOME/tmp" TEMP="$HOME/tmp" TMP="$HOME/tmp"

# ── Config ───────────────────────────────────────────────────────────
ROOT="$HOME/ece666"
NEW_TREE="$ROOT/gem5-v25-merged"
GEM5_TAG="v25.1.0.0"
GEM5_URL="https://github.com/gem5/gem5.git"
BUILD_TARGET="ECE666-X86"     # prof build_opts file; produces build/$BUILD_TARGET/gem5.opt

log() { printf '\n\033[1;36m[deploy] %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[ERROR] %s\033[0m\n' "$*" >&2; exit 1; }

# ── Sanity checks ────────────────────────────────────────────────────
log "Sanity: checking prereqs"
for cmd in git scons python3 g++; do
    command -v "$cmd" >/dev/null || die "missing: $cmd"
done
for f in "$PORT_DIR/patches/01-disable-hdf5.patch" \
         "$PORT_DIR/patches/02-disable-protobuf.patch" \
         "$PORT_DIR/patches/03-m5ops-libpath.patch" \
         "$PORT_DIR/build_opts/ECE666-X86" \
         "$PORT_DIR/build_opts/ECE666-ARM" \
         "$PORT_DIR/configs_spec/spec_se.py" \
         "$PORT_DIR/configs_spec/spec2k6_spec2k17.py"; do
    [ -f "$f" ] || die "port artifact missing: $f"
done

mkdir -p "$ROOT"

# ── Clone gem5 v25.1.0.0 ─────────────────────────────────────────────
if [ -d "$NEW_TREE/.git" ]; then
    log "Tree already exists at $NEW_TREE — skipping clone"
else
    log "Cloning $GEM5_TAG to $NEW_TREE"
    git clone --depth 50 --branch "$GEM5_TAG" "$GEM5_URL" "$NEW_TREE"
fi

cd "$NEW_TREE"

# ── Apply prof delta ─────────────────────────────────────────────────
log "Applying prof patches"
# Use -N to skip if already applied (idempotent re-runs)
for p in "$PORT_DIR/patches"/*.patch; do
    echo "  $p"
    patch -p1 -N --silent < "$p" || {
        # -N returns nonzero if already applied; check with --dry-run
        if patch --dry-run -R -p1 < "$p" >/dev/null 2>&1; then
            echo "    (already applied)"
        else
            die "patch failed: $p"
        fi
    }
done

log "Installing prof build_opts"
cp "$PORT_DIR/build_opts/ECE666-X86" build_opts/ECE666-X86
cp "$PORT_DIR/build_opts/ECE666-ARM" build_opts/ECE666-ARM

log "Installing prof SPEC configs"
mkdir -p configs/spec
cp "$PORT_DIR/configs_spec/spec_se.py"          configs/spec/
cp "$PORT_DIR/configs_spec/spec2k6_spec2k17.py" configs/spec/

# ── Build ────────────────────────────────────────────────────────────
log "Building $BUILD_TARGET (may take 20-60 min)"
JOBS="$(nproc 2>/dev/null || echo 4)"
scons "build/$BUILD_TARGET/gem5.opt" -j"$JOBS" 2>&1 | tee "$NEW_TREE/build_x86.log"

GEM5_BIN="$NEW_TREE/build/$BUILD_TARGET/gem5.opt"
[ -x "$GEM5_BIN" ] || die "build did not produce $GEM5_BIN"
log "Built: $GEM5_BIN"

# ── Smoke test: Step A (hello world SE) ──────────────────────────────
log "Running hello-world SE smoke test"
HELLO_DIR="$NEW_TREE/m5out_hello"
SE_CFG="$NEW_TREE/configs/deprecated/example/se.py"
HELLO_BIN="$NEW_TREE/tests/test-progs/hello/bin/x86/linux/hello"

[ -f "$SE_CFG" ]  || die "se.py not found at $SE_CFG"
[ -x "$HELLO_BIN" ] || die "hello binary not found at $HELLO_BIN (may need to build test-progs)"

mkdir -p "$HELLO_DIR"
"$GEM5_BIN" -d "$HELLO_DIR" "$SE_CFG" --cmd="$HELLO_BIN" \
    > "$HELLO_DIR/hello.stdout" 2> "$HELLO_DIR/hello.stderr"

if grep -q "Hello world" "$HELLO_DIR/simout" "$HELLO_DIR/hello.stdout" "$HELLO_DIR/hello.stderr" 2>/dev/null; then
    log "SMOKE TEST PASS — 'Hello world!' observed"
else
    die "SMOKE TEST FAIL — Hello world not found; see $HELLO_DIR/"
fi

# ── Summary ──────────────────────────────────────────────────────────
cat <<EOF

╔════════════════════════════════════════════════════════════════╗
║  DEPLOY COMPLETE                                               ║
╠════════════════════════════════════════════════════════════════╣
║  New tree : $NEW_TREE
║  Binary   : $GEM5_BIN
║  Smoke    : m5out_hello/ (Hello world! OK)
║
║  NEXT — Phase 1 full validation (FFT + old/new comparison):
║    bash $PORT_DIR/phase1_validate.sh
║
║  See FOLLOWUP.md in $PORT_DIR for known issues / caveats.
╚════════════════════════════════════════════════════════════════╝
EOF
