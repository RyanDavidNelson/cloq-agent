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

from dataclasses import dataclass


@dataclass(frozen=True)
class ArrayShape:
    """The recovered shape of an array-search loop's element access `mem[base + f(index)]`."""
    base_reg: str            # register holding the array base (e.g. "R_A0")
    index_reg: str           # register holding the loop index/counter (e.g. "R_A5")
    elem_bytes: int          # element width in bytes (4 for uint32)
    shift_form: bool         # True => `i << log2(bytes)` (slli); False => `bytes * i` (mul)

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


def decidability_block(shape: ArrayShape) -> str:
    """The `key_in_array` + `lt_impl_lt_or_eq` + `key_in_array_dec` Coq definitions for `shape`.
    Goes inside the timing functor (needs `memory`/`addr`/the load notation in scope). Verbatim the
    vendored proof's structure, with the element address specialised to the recovered shape."""
    ld = shape.load_notation
    addr_i = shape.addr_expr("i")
    addr_len = shape.addr_expr("len")
    return f"""\
  Definition key_in_array (mem : memory) (arr : addr) (key : N) (len : N) : Prop :=
      exists i, i < len /\\ mem {ld}[arr + ({addr_i})] = key.

  Lemma lt_impl_lt_or_eq : forall x y, x < 1 + y -> x = y \\/ x < y.
  Proof. lia. Qed.

  Definition N_peano_ind_Set (P : N -> Set) := N.peano_rect P.

  Fixpoint key_in_array_dec (mem : memory) (arr : addr) (key len : N)
          : {{key_in_array mem arr key len}} + {{~ key_in_array mem arr key len}}.
      induction len using N_peano_ind_Set.
      - right. intro. destruct H as (idx & Contra & _). lia.
      - destruct IHlen as [IN | NOT_IN].
          -- left. destruct IN as (idx & Lt & Eq). exists idx. split. lia. assumption.
          -- destruct (N.eq_dec (mem {ld}[arr + ({addr_len})]) key).
              + left. exists len. split. lia. assumption.
              + right. intro. destruct H as (idx & Lt & Eq).
                  assert (idx = len). {{
                  destruct (lt_impl_lt_or_eq idx len). lia.
                      subst. reflexivity.
                  exfalso. apply NOT_IN. exists idx. now split.
                  }} subst. contradiction.
  Qed."""


def timing_postcondition_block(shape: ArrayShape, time_of_search: str) -> str:
    """The found/not-found timing DISJUNCTION. `time_of_search` is the name of the pinned cycle
    closed form `time_of_<f> len (option index) t` (derived from cfg.loop_timing). The first-match
    `forall j < i, mem[..] <> key` clause makes the `Some i` the FIRST hit, so the bound is exact."""
    ld = shape.load_notation
    addr_i = shape.addr_expr("i")
    addr_j = shape.addr_expr("j")
    return f"""\
  Definition timing_postcondition (mem : memory) (arr : addr) (key : N) (len : N)
          (t : trace) : Prop :=
      (exists i, i < len /\\ mem {ld}[arr + ({addr_i})] = key /\\
          (forall j, j < i -> mem {ld}[arr + ({addr_j})] <> key) /\\
          {time_of_search} len (Some i) t) \\/
      ((~ exists i, i < len /\\ mem {ld}[arr + ({addr_i})] = key) /\\
          {time_of_search} len None t)."""


def case_split_tactic(mem_reg: str = "s' V_MEM32", base: str = "arr",
                      key: str = "key", length: str = "len") -> str:
    """The structural move the search proof turns on: decide membership, then prove each branch.
    `destruct (key_in_array_dec …) as [IN | NOT_IN]` fans the loop-exit arm into the found and
    not-found cases — the analogue of `destruct_inv` for a data-dependent early exit."""
    return f"destruct (key_in_array_dec ({mem_reg}) {base} {key} {length}) as [IN | NOT_IN]."
