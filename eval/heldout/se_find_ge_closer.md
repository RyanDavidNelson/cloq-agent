# se_find_ge (>=) closer — COMPLETE (standalone coqc-Qed `.vo`), and the generalization delta

Status: **the held-out se_find_ge search proof is a kernel-checked `.vo`.** A clean `coqc` of
`proofs/targets/Se_find_ge_gen.v` emits `Se_find_ge_gen.vo` with a real `Qed` (no "Open proofs
remain"); `Print Assumptions` rests only on the documented trust basis (`functional_extensionality`
+ the NEORV32 `T_inst_latency` / `T_data_latency` constants) — no `admit`, no proof-specific axiom.
Pinned (alongside se_find_eq) by `tests/test_se_find_closes.py`.

se_find_ge is the **predicate sibling** of se_find_eq: the same loop scanning an array, but testing
`arr[i] >= key` instead of `arr[i] == key`. The whole point of the target is generalization — it
measures that the decidability template and the closer are **not baked to `==`**, they parameterize
over the comparison. Reproduce: `python eval/heldout/build_se_find_ge.py` → `docker restart
docker-rocq-1`; compile with `make all` in `proofs/` (or the steps in `tests/test_se_find_closes.py`).

## The deterministic front is IDENTICAL (a predicate must not move the shape)

`se_find_ge.o` is **byte-identical** to `se_find_eq.o` except one instruction: the loop-body compare
branch at `0x24` is `bltu a3,a1` (ge) where se_find_eq has `bne a3,a1` (eq). Consequently:

* classify → search early-exit (same);
* `array_search_shape` → `base=R_A0, index=R_A4, stride=4, moving=R_A5, bound=R_A2, bound_kind=index`
  (**identical** — a comparison change does not touch the load/induction/bound recovery);
* `shape_premises` → `ALIGN (exists k', base = 4*k')`, `BOUND (4*n < 2^32)` (**identical**), and the
  premise gate discharges them (recovery oracle clean);
* `bottom_test_timing` → **identical except** the auto-derived body-compare constant: `ttbltu/tfbltu`
  where se_find_eq has `ttbne/tfbne` (the `bltu` vs `bne` opcode). Everything else — prologue,
  latch `tfbeq/ttbeq` (the `beq` bound test is unchanged), guard, both shutdowns — matches.

A difference here would have been a real finding; there was none. The predicate stayed in the
predicate.

## The template change (the only non-closer code touched)

`search_template` gained a tiny `Predicate` abstraction (`holds` / `neg` / `decide` strings) and two
instances; the decidability proof body is predicate-agnostic, so a new comparison is a 3-string
change, not a new proof:

| | `eq` (PRED_EQ) | `ge` (PRED_GE) |
|---|---|---|
| membership at found `i` | `mem[..] = key` | `key <= mem[..]` |
| first-match negation (`j<i`) | `mem[..] <> key` | `mem[..] < key` |
| decider (`destruct …`) | `N.eq_dec (mem[..]) key` | `N.leb_spec0 key (mem[..])` |

`N` has **no `le_dec` sumbool**, so ge uses `N.leb_spec0 key e : reflect (key <= e) (key <=? e)` —
its two constructors carry exactly `key <= e` / `~ key <= e`, so the `left … assumption` /
`right … contradiction` proof body is unchanged. The `eq` default is byte-for-byte what it was
(find_in_array / se_find_eq are untouched; verified by `template_defs` equality test).

## The generalization delta — which closer arms are verbatim, which are predicate-specific

The proof STRUCTURE is reused **verbatim** from `Se_find_eq_gen.v`: the top-level `destruct (len =? 0)`,
curly-brace `{ ... }` focus per `prove_invs`/`destruct_inv` arm, one order-independent
`all: solve [ c1 | c2 | c3 ]` per loop-body fan-out, and the closing `Unshelve. all: exact 0.` for
the two len=0 guard-path shelf evars (same shelf behaviour as se_find_eq).

The body compare surfaces as `BC : (mem[..] <? key) = b` (plain `N.ltb`, **no `negb`** — contrast
se_find_eq's `BC : negb (mem[..] =? key) = b`). The taken edge (`bltu`, continue) means `mem[..] <
key`; the fall-through (found/return) means `mem[..] >= key`.

| arm | reuse | what changed |
|---|---|---|
| len=0 guard branch (both sub-arms) | **verbatim** | — (guard only, no element compare) |
| len<>0 ARM 0 (`0x0`→body, i=0) | **verbatim** | — (guard only) |
| ARM 1 found-here (`0x2c`) | predicate-specific | `apply negb_false_iff in BC; now apply N.eqb_eq in BC` → `apply N.ltb_ge in BC; exact BC` (proves `key <= mem[..]`) |
| ARM 1 i+1 continuation (IN and NOT_IN) | predicate-specific (one lemma) | `apply negb_true_iff in BC; apply N.eqb_neq in BC` → `apply N.ltb_lt in BC`; the first-match `exact BC` then discharges `mem[..] < key` |
| ARM 1 bound-exit contradiction (IN) | predicate-specific | the eq version closed by `exact (NotFound idx L IE)` / `exact (BC IE)` (equality mismatch); ge closes by `apply N.ltb_lt in BC` then `lia` against `key <= mem[..]` (an order contradiction, not a disequality) |
| ARM 1 found-position contradiction (NOT_IN) | predicate-specific | `apply negb_false_iff …` → `apply N.ltb_ge in BC; exact BC` |
| ARM 1 not-found exit / time None (`0x30`) | **verbatim** | only the `time_of` name (`cloq_time_of_se_find_ge`); the `~exists` + `destruct len` + `hammer` is unchanged |

So the delta is exactly: **the three comparison-touching leaves** swap `negb_*` + `N.eqb_*` for
`N.ltb_ge` / `N.ltb_lt`, and the IN bound-exit contradiction becomes an order-contradiction (`lia`)
instead of a disequality-contradiction (`exact`). Everything that is about the loop SHAPE or the
TIMING — guard arms, prologue/latch stepping, the not-found exit, the shelf discharge — is reused
verbatim. The closer is predicate-parametric over `{==, >=}`; what is predicate-specific is precisely
the handful of hypotheses that mention the compare, exactly as a clean generalization should be.

## Lessons (delta vs se_find_eq_closer.md)

* the `bltu` branch condition is `(elem <? key)` with **no `negb`** wrapper (eq's `bne` gave
  `negb (… =? key)`); reach for `N.ltb_lt` / `N.ltb_ge`, not `negb_*`/`N.eqb_*`.
* `N.le_dec` does not exist for `N`; `N.leb_spec0` is the sumbool-shaped decider (a `reflect`).
* a `>=` (order) contradiction is closed by `lia` over `key <= e` and `e < key`, not by `exact` of a
  disequality witness — the one place the closer's *shape* (not just a lemma name) differs.
