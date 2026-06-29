r"""Decidability TEMPLATE for array-search (early-exit) loops — Phase 2.

`docs/RESULTS.md` called `key_in_array_dec` "bespoke, not genericizable", but find_in_array and
find_in_array_opt carry *near-identical* decidability lemmas: they differ only in the element
address expression — `arr + (i << 2)` vs `arr + 4 * i`. So it is a template parameterized by the
recovered array shape (base register, element stride, access form). This module emits, for a
recovered shape:

  * `key_in_array`            — the membership predicate `exists i, i < len /\ mem[arr+f(i)] = key`;
  * `lt_impl_lt_or_eq`        — the index trichotomy the case-split needs;
  * `key_in_array_dec`        — DECIDABILITY of membership (induction on len, `N.eq_dec` per step);
  * `timing_postcondition`    — the found/not-found DISJUNCTION over the pinned cycle closed form.

The discharge then case-splits with `destruct (key_in_array_dec …)` (see `case_split_tactic`).
Emitting these from the shape removes the per-program hand-written copy: a new array-search target
gets its decidability scaffold for free. (Linked-list search — find_in_list — needs list theory,
and the cyclic vListInsert needs uniqueness-in-a-cycle; neither is covered here.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The definition names the template OWNS. When the lifted program is reused from a vendored functor
# that already defines these, the emitted copies are namespaced (prefix `cloq_`) and the invariant /
# proof are rewritten to the prefixed names so there is no clash and the proof drives OUR defs.
TEMPLATE_NAMES = (
    "timing_postcondition", "key_in_array_dec", "key_in_array",
    "lt_impl_lt_or_eq", "N_peano_ind_Set",
)


@dataclass(frozen=True)
class ArrayShape:
    """The recovered shape of a loop's element access `mem[base + f(index)]`.

    Covers two physical forms the lifter sees for the SAME logical `base + stride*i` access:
      * indexed/recomputed (`slli;add;lw`) — `shift_form=True`, `moving_reg=None`;
      * running pointer (`lw 0(p); addi p,p,stride`) — `shift_form=False`, `moving_reg` set to the
        pointer register and `base_reg` traced to the register holding its ENTRY value (the base).
    The trip count is uniform-ish: a separate `i<len` counter gives `bound_kind="index"` (the loop
    runs `bound_reg` times); a pointer bound `p<end` gives `bound_kind="pointer_range"` (it runs
    `(bound_reg - base)/stride` times). Both reduce to one `(reg, step)` induction = `(moving or
    index reg, stride)`."""
    base_reg: str            # register holding the array base at loop entry (e.g. "R_A0")
    index_reg: str           # the induction register: counter, or the running pointer
    elem_bytes: int          # element width in bytes (4 for uint32) == stride for these targets
    shift_form: bool         # True => `i << log2(bytes)` (slli); False => `bytes * i` / running ptr
    moving_reg: str | None = None   # the running-pointer register, or None for the indexed form
    bound_reg: str | None = None    # the register bounding the loop (len, or the end pointer)
    bound_kind: str = "index"       # "index" (runs bound_reg times) | "pointer_range" ((end-base)/stride)

    def addr_expr(self, index: str) -> str:
        """The Coq address offset expression for element `index` (a variable name or literal)."""
        if self.shift_form:
            shift = (self.elem_bytes - 1).bit_length()   # 4 -> 2
            return f"{index} << {shift}"
        return f"{self.elem_bytes} * {index}"

    @property
    def load_notation(self) -> str:
        """The Picinae fixed-width load notation for this element width (Ⓓ = 32-bit word)."""
        return {1: "Ⓑ", 2: "Ⓦ", 4: "Ⓓ", 8: "Ⓠ"}.get(self.elem_bytes, "Ⓓ")


def time_of_definition(name: str, trip: str, t) -> str:
    """Render a `SearchTiming` (from `cfg.search_loop_timing`) as the Coq disjunctive closed form.
    `trip` is the not-found iteration count (`len`, or the pointer-range `(end-base)/stride`); the
    found arm counts `i`, the load index. Emits the same shape as the vendored `time_of_<fn>`."""
    return (
        f"  Definition {name} ({trip} : N) (found_idx : option N) (t : trace) :=\n"
        f"      cycle_count_of_trace t =\n"
        f"          {t.setup} +\n"
        f"          (match found_idx with None => {trip} | Some i => i end) * ({t.body}) +\n"
        f"          (match found_idx with None => {t.notfound_partial} "
        f"| Some _ => {t.found_partial} end) +\n"
        f"          {t.shutdown}."
    )


def time_of_bottom_test(name: str, trip: str, b) -> str:
    """Render a `BottomTestTiming` as the Coq closed form for a rotated do-while + guard search.
    A TWO-LEVEL match: on found_idx, then on `trip` inside the not-found arm so `len = 0` (the
    guard that skips the body) is covered — keeping `time_of` total over its domain, the single
    authority on cost, and forcing the closer (and the premise gate) to discharge the boundary."""
    return (
        f"  Definition {name} ({trip} : N) (found_idx : option N) (t : trace) :=\n"
        f"      cycle_count_of_trace t =\n"
        f"      match found_idx with\n"
        f"      | Some i => {b.pro} + i * ({b.body_cont}) + {b.found_partial} + {b.shut_f}\n"
        f"      | None => match {trip} with\n"
        f"                | 0 => {b.guard}\n"
        f"                | _ => {b.pro} + ({trip} - 1) * ({b.body_cont}) + {b.body_exit} "
        f"+ {b.shut_nf}\n"
        f"                end\n"
        f"      end."
    )


def shape_premises(shape: ArrayShape, base: str = "base", trip: str = "n",
                   endp: str = "endp") -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """The input well-formedness premises IMPLIED by a recovered shape, parameterized by the
    recovered stride — returned as (binders, premises). Feeding these through the premise gate is
    the GAP-1 recovery oracle: a wrong stride yields a degenerate or contradictory obligation that
    fails to discharge, so a recovery bug is caught at generation time, not three gaps later.

      * index form        — alignment `exists k', base = stride*k'` and bound `stride*n < 2^32`;
      * pointer_range form — alignment and the end pointer reachable in whole strides from base:
        `exists k', end = base + stride*k'`.
    """
    s = shape.elem_bytes
    align = ("ALIGN", f"exists k', {base} = {s} * k'")
    if shape.bound_kind == "pointer_range":
        return ([(base, "N"), (endp, "N")],
                [align, ("RANGE", f"exists k', {endp} = {base} + {s} * k'")])
    return ([(base, "N"), (trip, "N")], [align, ("BOUND", f"{s} * {trip} < 2^32")])


def decidability_block(shape: ArrayShape, prefix: str = "") -> str:
    """The `key_in_array` + `lt_impl_lt_or_eq` + `key_in_array_dec` Coq definitions for `shape`.
    Goes inside the timing functor (needs `memory`/`addr`/the load notation in scope). Verbatim the
    vendored proof's structure, with the element address specialised to the recovered shape.

    `prefix` namespaces the emitted names (e.g. `cloq_`) so they do not clash with identically-named
    definitions pulled in from a reused vendored functor (the lifted program still comes from there;
    only the decidability is emitted)."""
    ld = shape.load_notation
    addr_i = shape.addr_expr("i")
    addr_len = shape.addr_expr("len")
    p = prefix
    return f"""\
  Definition {p}key_in_array (mem : memory) (arr : addr) (key : N) (len : N) : Prop :=
      exists i, i < len /\\ mem {ld}[arr + ({addr_i})] = key.

  Lemma {p}lt_impl_lt_or_eq : forall x y, x < 1 + y -> x = y \\/ x < y.
  Proof. lia. Qed.

  Definition {p}N_peano_ind_Set (P : N -> Set) := N.peano_rect P.

  Fixpoint {p}key_in_array_dec (mem : memory) (arr : addr) (key len : N)
          : {{{p}key_in_array mem arr key len}} + {{~ {p}key_in_array mem arr key len}}.
      induction len using {p}N_peano_ind_Set.
      - right. intro. destruct H as (idx & Contra & _). lia.
      - destruct IHlen as [IN | NOT_IN].
          -- left. destruct IN as (idx & Lt & Eq). exists idx. split. lia. assumption.
          -- destruct (N.eq_dec (mem {ld}[arr + ({addr_len})]) key).
              + left. exists len. split. lia. assumption.
              + right. intro. destruct H as (idx & Lt & Eq).
                  assert (idx = len). {{
                  destruct ({p}lt_impl_lt_or_eq idx len). lia.
                      subst. reflexivity.
                  exfalso. apply NOT_IN. exists idx. now split.
                  }} subst. contradiction.
  Qed."""


def timing_postcondition_block(shape: ArrayShape, time_of_search: str, prefix: str = "") -> str:
    """The found/not-found timing DISJUNCTION. `time_of_search` is the name of the pinned cycle
    closed form `time_of_<f> len (option index) t` (derived from cfg.loop_timing). The first-match
    `forall j < i, mem[..] <> key` clause makes the `Some i` the FIRST hit, so the bound is exact."""
    ld = shape.load_notation
    addr_i = shape.addr_expr("i")
    addr_j = shape.addr_expr("j")
    return f"""\
  Definition {prefix}timing_postcondition (mem : memory) (arr : addr) (key : N) (len : N)
          (t : trace) : Prop :=
      (exists i, i < len /\\ mem {ld}[arr + ({addr_i})] = key /\\
          (forall j, j < i -> mem {ld}[arr + ({addr_j})] <> key) /\\
          {time_of_search} len (Some i) t) \\/
      ((~ exists i, i < len /\\ mem {ld}[arr + ({addr_i})] = key) /\\
          {time_of_search} len None t)."""


def case_split_tactic(mem_reg: str = "s' V_MEM32", base: str = "arr",
                      key: str = "key", length: str = "len", prefix: str = "") -> str:
    """The structural move the search proof turns on: decide membership, then prove each branch.
    `destruct (key_in_array_dec …) as [IN | NOT_IN]` fans the loop-exit arm into the found and
    not-found cases — the analogue of `destruct_inv` for a data-dependent early exit."""
    return f"destruct ({prefix}key_in_array_dec ({mem_reg}) {base} {key} {length}) as [IN | NOT_IN]."


def template_defs(shape: ArrayShape, time_of_search: str, prefix: str = "cloq_") -> str:
    """The full emitted block for an array-search target: the decidability lemmas + the found/
    not-found timing disjunction, namespaced by `prefix`. Injected into the generated scaffold
    (`TargetSpec.search_defs`) so the proof's case-split runs on emitted defs, not vendored ones."""
    return decidability_block(shape, prefix) + "\n\n" + \
        timing_postcondition_block(shape, time_of_search, prefix)


def prefix_template_names(text: str, prefix: str = "cloq_") -> str:
    """Rewrite the template-owned identifiers in `text` (an invariant or proof script) to their
    `prefix`ed names, so a reused invariant/proof references the emitted defs. Whole-identifier
    only (word boundaries), so `key_in_array_dec` is not mangled by the `key_in_array` rule."""
    for name in TEMPLATE_NAMES:                      # longest-first is enforced by \b anyway
        text = re.sub(rf"\b{name}\b", prefix + name, text)
    return text
