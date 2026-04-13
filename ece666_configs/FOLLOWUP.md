# FOLLOWUP — port of ECE666_MiniProj → gem5 v25.1.0.0

Read this before running `deploy_to_qstruct.sh`. Nothing below is a
blocker; they are decisions and risks I couldn't resolve without you.

---

## TL;DR

- Prof delta is tiny and benign: **7 files across 5 commits, zero C++
  SimObjects**. It's build flags + run scripts, not a gem5 subsystem.
- The port to v25.1.0.0 is already done — three `.patch` files,
  `build_opts/ECE666-{X86,ARM}`, and the two SPEC run scripts.
- One-shot deploy: `bash port/deploy_to_qstruct.sh` on qstruct.

---

## Exactly what the professor changed

5 commits on top of gem5 v22.0.0.2:

| Commit   | What                       | Files                                                      |
|----------|----------------------------|------------------------------------------------------------|
| 5395c45  | ECE565+ECN Fall 2022       | `build_opts/ECE565-ARM`, `build_opts/ECE565-X86`, `src/base/stats/SConsopts`, `src/proto/SConsopts` |
| 85205fc  | m5 ops library path fix    | `util/m5/SConstruct`                                        |
| 4aeacf7  | run scripts added          | `configs/spec/spec2k6_spec2k17.py`, `configs/spec/spec_se.py` |
| 8edd72e  | run script corrections     | same as above + `.gitignore`                                 |
| 3eebc8d  | SpecCPU 2K17 added/tested  | `configs/spec/spec2k6_spec2k17.py`                          |

The ECN-specific modifications disable HDF5 and protobuf checks (those
libs aren't on ECN machines) and hack `/usr/lib64` into `util/m5`'s
library search path.

---

## What the port does

1. Translates prof's v22-format `build_opts/ECE565-{ARM,X86}` into
   v25.1 kconfig format and renames to `ECE666-{X86,ARM}` (that's the
   name you mentioned for the old-tree build).
2. Reapplies HDF5 / protobuf disables to v25.1's (slightly changed)
   `SConsopts` files.
3. Reapplies the `/usr/lib64` LIBFLAGS patch to v25.1's `util/m5/SConstruct`.
4. Drops the two SPEC run scripts into `configs/spec/` verbatim.

---

## Known risks / things to check

### 1. "ECE666" vs "ECE565" build target name

The prof's committed build_opts file is called **`ECE565-X86`**. You
told me the old-tree binary lives at `build/ECE666/gem5.opt`. Three
possibilities:

  (a) You renamed it on qstruct but didn't commit.
  (b) There's a separate uncommitted `build_opts/ECE666` on qstruct I
      can't see from this local clone.
  (c) You misremembered and the actual path is
      `build/ECE565-X86/gem5.opt` or similar.

**Port behavior:** the deploy script installs `build_opts/ECE666-X86`
and builds `build/ECE666-X86/gem5.opt` (**note the -X86 suffix** — matches
the prof's scheme). If you need the bare `ECE666` name, symlink or copy
the file.

**For Phase 1 validation**, export `OLD_GEM5` to whatever the old-tree
binary actually is — do not assume `build/ECE666/gem5.opt` if Phase 0
inspection shows otherwise.

### 2. CPU_MODELS line removed

Prof's build_opts contained:

    CPU_MODELS = 'AtomicSimpleCPU,O3CPU,TimingSimpleCPU,MinorCPU'

v25.1's kconfig build system has no `CPU_MODELS` option — all CPU
models are built when their source is present. I dropped the line. All
four CPU types will still be available.

### 3. SPEC Python scripts: API drift risk

The prof's `spec_se.py` and `spec2k6_spec2k17.py` were written against
v22. I confirmed the main imports still exist in v25.1:

- `configs/common/Options.py` — `addCommonOptions`, `addSEOptions` present
- `configs/common/Simulation.py`, `CacheConfig.py`, `CpuConfig.py`, etc. — all present
- `configs/common/cpu2000.py` — present
- `configs/ruby/Ruby.py` — present
- `config_filesystem` in `FileSystemConfig.py` — present

But individual option names or argument signatures may have drifted
between v22 and v25.1. The scripts will likely need small fixes the
first time you try to run actual SPEC. **That's out of scope for Phase
1 validation** (we're validating with Splash-4 FFT using stock `se.py`,
not SPEC). Treat SPEC as a Phase 3 problem.

### 4. Protobuf disable

v25.1's `src/proto/SConsopts` changed slightly (uses `bool(...)` wrapper
now). The patch is updated to match. Effect is the same: protobuf
support disabled, no tracing/replay features in the built gem5. That
matches the prof's original intent.

### 5. v25.1-specific warnings (not prof-related)

- `configs/example/se.py` moved to `configs/deprecated/example/se.py`
  in v25.1. All scripts updated to use the new path. Still works the
  same way.
- CPU model naming/inheritance had some churn in v25.1 release notes.
  Since prof didn't touch any CPU code, this does not affect us.
- Walk-cache behavior changed in v25.1. Also not touched by prof.

### 6. Coverage gate — ANSWERED

Because the prof delta is config-only with no C++ SimObjects, **a
standard `se.py --caches` FFT run fully covers the prof contribution**
— there is no hidden prof code to miss. The validation is simply:

- does the new-tree build succeed under `build_opts/ECE666-X86`?
- does FFT execute correctly?
- are stats close enough between old and new that we believe the
  merge didn't introduce regressions?

You do **not** need to find a benchmark that "exercises a prof SimObject"
— there aren't any.

---

## What you need to do

1. **Transfer port/ to qstruct**:
   ```bash
   scp -r /Users/mac/Documents/Coding/GPU/GEM5/port jghanem@qstruct.ecn.purdue.edu:~/ece666/
   ```

2. **Run the deploy**:
   ```bash
   ssh jghanem@qstruct 'bash ~/ece666/port/deploy_to_qstruct.sh'
   ```
   Expected: clone → patch → build (20–60 min) → hello-world smoke test PASS.

3. **Find your old-tree gem5.opt**:
   ```bash
   ssh jghanem@qstruct 'ls ~/ece666/gem5-Spr2023/build/*/gem5.opt'
   ```
   Use whichever path comes back as `OLD_GEM5` below.

4. **Run Phase 1 validation**:
   ```bash
   ssh jghanem@qstruct bash -s <<'EOF'
   export OLD_GEM5=~/ece666/gem5-Spr2023/build/ECE666/gem5.opt   # or actual path
   export FFT_BIN=~/ece666/benchmarks/Splash-4/.../FFT            # from build_splash4.sh
   bash ~/ece666/port/phase1_validate.sh
   EOF
   ```

5. **Paste the pass/fail table back to me**, plus `build_x86.log` tail
   if the build failed.

---

## What I could not do without qstruct

- **Build gem5 for real.** All I verified is that the three patches
  apply cleanly to v25.1's tree (`patch --dry-run`). The actual
  compile step happens on qstruct.
- **Run the validation.** Same reason.
- **Confirm `OLD_GEM5` path.** Your `gem5-Spr2023/build/` contents are
  not in the clone I have.
- **Rebuild SPEC scripts against v25.1 API.** Blocked until you
  actually try to run SPEC — which is outside Phase 1 scope.
