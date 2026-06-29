"""Minimal control-flow recovery for the invariant-synthesis prompt.

The agent needs the *shape* of the code: basic blocks, branch targets, and which edges are
back-edges (loops), because a Cloq invariant set has one invariant per loop header plus a pre-
and post-condition. This is intentionally lightweight — it parses a RISC-V `objdump -d` listing
(or a Picinæ IL address listing) into blocks and finds back-edges. It is a prompt aid, not a
trusted component: the proof's soundness comes from Rocq, not from this parser.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_INSN = re.compile(r"^\s*([0-9a-fA-F]+):\s+[0-9a-fA-F ]+\t(\S+)\s*(.*)$")
_BRANCH = {"beq", "bne", "blt", "bge", "bltu", "bgeu", "beqz", "bnez", "j", "jal", "jalr", "ret"}
_TARGET = re.compile(r"([0-9a-fA-F]+)\s*(?:<|$)")

# RISC-V mnemonic -> Cloq per-instruction timing constant (see vendor/.../riscv/RISCVTiming.v).
# The timing of one instruction is fixed by its opcode, so a block's cycle cost is the SUM of these
# — derivable from the CFG, not guessed. Shifts carry the shift amount; conditional branches have a
# taken (tt<op>) and a fall-through (tf<op>) form chosen by which edge stays in the loop.
_TCONST = {
    "lw": "tlw", "sw": "tsw", "addi": "taddi", "add": "tadd", "sub": "tsub",
    # sub-word memory ops (NEORV32 models tlbu/tlb/tlhu/tlh/tsb/tsh = 4 + T_data_latency)
    "lb": "tlb", "lbu": "tlbu", "lh": "tlh", "lhu": "tlhu", "sb": "tsb", "sh": "tsh",
    "xor": "txor", "xori": "txori", "and": "tand", "andi": "tandi", "or": "tor", "ori": "tori",
    "jal": "tjal", "j": "tjal", "jalr": "tjalr", "ret": "tjalr", "lui": "tlui", "auipc": "tauipc",
    "mul": "tmul", "sll": "tsll", "slt": "tslt", "sltu": "tsltu",
}
_SHIFT = {"slli": "tslli", "srli": "tsrli", "srai": "tsrai"}
_BRANCH_OPS = {"beq", "bne", "blt", "bge", "bltu", "bgeu"}
_LOADS = {"lw", "lh", "lhu", "lb", "lbu"}
_STORES = {"sw", "sh", "sb"}
_LOAD_BYTES = {"lw": 4, "lh": 2, "lhu": 2, "lb": 1, "lbu": 1}


def _reg(name: str) -> str:
    """ABI register name (e.g. `a5`, `t0`) -> Picinae register (`R_A5`, `R_T0`)."""
    return f"R_{name.strip().upper()}"


def _imm(tok: str) -> int | None:
    """Parse an immediate operand (`4`, `0x4`, `-1`) -> int, or None if it is not a literal."""
    try:
        return int(tok.strip(), 0)
    except ValueError:
        return None


def _shamt(operands: str) -> str:
    last = operands.split(",")[-1].strip()
    try:
        return str(int(last, 0))
    except ValueError:
        return last


def _insn_tconst(ins: "Insn", taken: bool | None) -> str | None:
    """The Coq timing term for one instruction (None if it has no modeled cost)."""
    m = ins.mnemonic
    if m in _SHIFT:
        return f"{_SHIFT[m]} {_shamt(ins.operands)}"
    if m in _BRANCH_OPS:
        return f"{'tt' if taken else 'tf'}{m}"
    return _TCONST.get(m)


@dataclass
class SearchTiming:
    """The disjunctive closed form of a search loop: `setup + n*body + partial + shutdown`, where
    `n = (Some i => i | None => trip)` and only `partial` forks between the found / not-found arms."""
    setup: str            # entry..header straight-line cost
    body: str             # one full iteration (both branches stay in-loop)
    found_partial: str    # last partial iteration when the data-dependent match exits
    notfound_partial: str # last partial iteration when the structural bound exits
    shutdown: str         # exit-target..return straight-line cost


@dataclass
class Insn:
    addr: int
    mnemonic: str
    operands: str


@dataclass
class Block:
    start: int
    insns: list[Insn] = field(default_factory=list)
    succ: list[int] = field(default_factory=list)


@dataclass
class SkeletonPlan:
    """A CFG-derived invariant skeleton: addresses + match scaffold fixed, loop/entry arms holes.

    The whole point of this object is the soundness boundary: `hole_addrs`/`exit_addrs` and the
    match structure come from the CFG, and `postcondition` is pinned from the trusted spec. The
    model only ever supplies the bodies of the hole arms; `fill()` re-asserts everything else by
    construction, so a model that tampers with an address or the postcondition cannot leak through.
    """
    inv_name: str
    params: list[tuple[str, ...]]
    hole_addrs: list[int]            # entry + loop headers — the model fills these
    exit_addrs: list[int]            # exit/post — pinned from the spec, never a hole
    postcondition: str               # the pinned exit-arm proposition (from the spec)
    prompt_text: str                 # the skeleton-with-holes, shown to the model

    def fill(self, fills: dict[int, str]) -> str:
        """Render the completed Definition: model `fills` for the holes, pinned postcondition
        for every exit arm. Missing holes are left as an obvious sentinel so the proof fails
        (and the orchestrator retries) rather than silently admitting a malformed invariant."""
        arms: list[tuple[int, str]] = []
        for a in self.hole_addrs:
            arms.append((a, fills.get(a, f"(* UNFILLED HOLE 0x{a:x} *) False")))
        for a in self.exit_addrs:
            arms.append((a, self.postcondition))
        return _render_definition(self.inv_name, self.params, arms)


@dataclass
class CFG:
    blocks: dict[int, Block]
    entry: int
    back_edges: list[tuple[int, int]]   # (from_block_start, to_block_start) == loop headers

    @property
    def loop_headers(self) -> list[int]:
        return sorted({to for _, to in self.back_edges})

    def exit_points(self) -> list[int]:
        """Terminal blocks (a `ret`/no-successor block) — the program's exit addresses. With
        returns made leaders, these block starts are the actual return-instruction addresses."""
        return sorted(s for s, b in self.blocks.items() if not b.succ)

    def join_points(self) -> list[int]:
        """Block starts with more than one predecessor (excluding the entry): control-flow merges
        where a cut-point invariant is needed even though it is neither a loop header nor an exit
        (e.g. the two arms of an `if` rejoining). Derived purely from the CFG, never the model."""
        preds: dict[int, int] = {}
        for b in self.blocks.values():
            for t in b.succ:
                if t is not None:
                    preds[t] = preds.get(t, 0) + 1
        return sorted(a for a, c in preds.items() if c > 1 and a != self.entry and a in self.blocks)

    def invariant_points(self) -> list[int]:
        """Ordered, de-duplicated invariant-point addresses, derived purely from the CFG: the
        entry, every loop header, every control-flow join, and every exit. Never from the model."""
        return sorted({self.entry, *self.loop_headers, *self.join_points(), *self.exit_points()})

    def _preds(self) -> dict[int, list[int]]:
        preds: dict[int, list[int]] = {s: [] for s in self.blocks}
        for s, b in self.blocks.items():
            for t in b.succ:
                if t in preds:
                    preds[t].append(s)
        return preds

    def natural_loop(self, header: int) -> set[int]:
        """Public view of the natural-loop block set for `header` (for the C-intake classifier)."""
        return self._natural_loop(header)

    def loop_exit_edges(self, header: int) -> list[tuple[int, int]]:
        """Edges (src, dst) that leave the natural loop of `header`. A pure counter loop has
        exactly one such edge (the loop-condition branch); a data-dependent early-exit (search)
        loop has more. Derived from the CFG, never the model."""
        loop = self._natural_loop(header)
        out: list[tuple[int, int]] = []
        for s in loop:
            for t in self.blocks[s].succ:
                if t is not None and t not in loop:
                    out.append((s, t))
        return out

    def straightline_cycles(self) -> str:
        """The summed Coq timing term for a straight-line (loop-free) body: the per-instruction
        constants of every instruction that executes before an exit/return, in address order. This
        is the pinned WCET closed form for a straight-line function — CFG-derived, so the model
        never supplies it. Empty string if no instruction carries a modeled cost."""
        exits = set(self.exit_points())
        terms: list[str] = []
        all_insns = sorted((i for b in self.blocks.values() for i in b.insns), key=lambda i: i.addr)
        for ins in all_insns:
            if ins.addr in exits:
                continue  # the exit arm holds the count of everything BEFORE the return
            term = _insn_tconst(ins, None)
            if term:
                terms.append(term)
        return " + ".join(terms)

    # --- loop-shape analysis for the synthesis skeleton (fixes #2-#4) -------------------------
    def induction_var(self, header: int) -> tuple[str, int] | None:
        """Detect the loop's induction variable: a register incremented by a constant each
        iteration (`addi rX, rX, k` / `c.addi rX, k`). Returns (R_<reg>, step) or None.
        Used to turn the loop-arm hole into a concrete `exists i, (s R_X) = base + i*step` skeleton
        (fix #2: array/pointer loops)."""
        for ins in sorted((i for s in self._natural_loop(header) for i in self.blocks[s].insns),
                          key=lambda i: i.addr):
            if ins.mnemonic in ("addi", "c.addi"):
                ops = [o.strip() for o in ins.operands.split(",")]
                if len(ops) >= 3 and ops[0] == ops[1]:
                    try:
                        return _reg(ops[0]), int(ops[2], 0)
                    except ValueError:
                        continue
                if len(ops) == 2 and ins.mnemonic == "c.addi" and ops[0]:
                    try:
                        return _reg(ops[0]), int(ops[1], 0)
                    except ValueError:
                        continue
        return None

    def _loop_inductions(self, header: int) -> list[tuple[str, int]]:
        """Every constant-increment induction in the loop: (R_<reg>, step) for each `addi rX,rX,k`.
        Both the index counter (`addi i,i,1`) and a strength-reduced data pointer (`addi p,p,4`)
        show up here — recovery is uniform over them (GAP 1)."""
        out: list[tuple[str, int]] = []
        for ins in sorted((i for s in self._natural_loop(header) for i in self.blocks[s].insns),
                          key=lambda i: i.addr):
            if ins.mnemonic not in ("addi", "c.addi"):
                continue
            ops = [o.strip() for o in ins.operands.split(",")]
            if len(ops) >= 3 and ops[0] == ops[1]:
                step = _imm(ops[2])
            elif len(ops) == 2 and ins.mnemonic == "c.addi":
                step = _imm(ops[1])
            else:
                step = None
            if step is not None:
                out.append((_reg(ops[0]), step))
        return out

    def _reaching_param(self, addr: int, reg: str) -> str:
        """Resolve `reg` to the parameter register whose ENTRY value it holds at program point
        `addr` — following `mv`/`addi rd,rs,0` copies backwards from the latest definition strictly
        before `addr`. This is what disambiguates gcc's register reuse: in se_find_eq `a0` is `arr`
        at the pointer init (0x0) but `len` at the bound branch (after `mv a0,a2`)."""
        defs = sorted((i for b in self.blocks.values() for i in b.insns
                       if i.addr < addr and i.mnemonic in ("mv", "c.mv", "addi", "c.addi")),
                      key=lambda i: i.addr)
        cur = reg
        progressed = True
        while progressed:
            progressed = False
            for ins in reversed(defs):                       # latest def of `cur` before `addr`
                ops = [o.strip() for o in ins.operands.split(",")]
                if not ops or _reg(ops[0]) != cur:
                    continue
                if ins.mnemonic in ("mv", "c.mv") and len(ops) == 2:
                    cur, progressed = _reg(ops[1]), True
                elif ins.mnemonic in ("addi", "c.addi") and len(ops) >= 3 and _imm(ops[2]) == 0:
                    cur, progressed = _reg(ops[1]), True
                break                                        # non-copy def: `cur` settles here
        return cur

    def _loop_bound(self, header: int, ind_reg: str) -> tuple[str, int] | None:
        """The (limit-register, branch-addr) of the exit branch that bounds `ind_reg`: a conditional
        branch comparing `ind_reg` against another register, in a block that can leave the loop."""
        loop = self._natural_loop(header)
        for start in sorted(loop):
            blk = self.blocks[start]
            if all(s in loop for s in blk.succ):             # not an exiting block
                continue
            for ins in blk.insns:
                if ins.mnemonic not in _BRANCH_OPS:
                    continue
                ops = [o.strip() for o in ins.operands.split(",")]
                if len(ops) < 2:
                    continue
                r0, r1 = _reg(ops[0]), _reg(ops[1])
                if ind_reg in (r0, r1):
                    return (r1 if r0 == ind_reg else r0), ins.addr
        return None

    def array_search_shape(self, header: int):
        """Recover the element-access shape `mem[base + stride*i]` of an array/search loop, for the
        decidability template + timing. Returns a `search_template.ArrayShape` or None.

        Two physical forms, one recovery (GAP 1):
          * RUNNING POINTER (gcc -O2): `lw v, 0(p)` with `addi p,p,stride` — `p` is an induction
            register; base = the param `p` was initialised from, stride = the increment;
          * INDEXED/recomputed (vendored find_in_array): `slli off,i,k ; add ea,base,off ; lw v,0(ea)`.
        Trip count: a separate `i<len` counter -> `bound_kind="index"`; a pointer bound `p<end` ->
        `bound_kind="pointer_range"`. Register reuse is resolved via reaching-defs (`_reaching_param`)."""
        from .search_template import ArrayShape

        loop_insns = sorted((i for s in self._natural_loop(header) for i in self.blocks[s].insns),
                            key=lambda i: i.addr)
        load = next((i for i in loop_insns if i.mnemonic in _LOADS), None)
        if load is None:
            return None
        elem_bytes = _LOAD_BYTES.get(load.mnemonic, 4)
        ld_ops = [o.strip() for o in load.operands.split(",")]
        m = re.search(r"\(([^)]+)\)", ld_ops[-1]) if len(ld_ops) >= 2 else None
        if not m:
            return None
        ea = _reg(m.group(1))                                # the load's address register
        inductions = self._loop_inductions(header)

        # --- running-pointer form: the load's address register is itself incremented in-loop ---
        ptr = next(((r, s) for r, s in inductions if r == ea), None)
        if ptr is not None:
            # For a contiguous word walk the pointer increment (ptr[1]) equals the load width;
            # elem_bytes drives both the load notation and the `base + elem_bytes*i` address. (A
            # byte stride / mismatched increment is GAP 2's problem, deliberately out of scope here.)
            init = self._reg_init_before_loop(header, ea)    # (src_reg, addr) of `mv ea, src`
            base_reg = self._reaching_param(init[1], init[0]) if init else ea
            counter = next(((r, s) for r, s in inductions if r != ea and s == 1), None)
            ind = counter[0] if counter else ea
            bound = self._loop_bound(header, ind)
            bound_reg = self._reaching_param(bound[1], bound[0]) if bound else None
            return ArrayShape(base_reg=base_reg, index_reg=ind, elem_bytes=elem_bytes,
                              shift_form=False, moving_reg=ea, bound_reg=bound_reg,
                              bound_kind="index" if counter else "pointer_range")

        # --- indexed/recomputed form: ea = add base, (slli idx, k) ---
        add = next((i for i in loop_insns if i.mnemonic in ("add", "c.add")
                    and _reg(i.operands.split(",")[0]) == ea), None)
        if add is None:
            return None
        a_ops = [_reg(o) for o in add.operands.split(",")]
        iv = self.induction_var(header)
        index_reg = iv[0] if iv else None
        bound = self._loop_bound(header, index_reg) if index_reg else None
        bound_reg = self._reaching_param(bound[1], bound[0]) if bound else None
        slli = next((i for i in loop_insns if i.mnemonic in ("slli", "c.slli")), None)
        if slli is not None:
            s_ops = [o.strip() for o in slli.operands.split(",")]
            off_reg = _reg(s_ops[0])
            base_reg = next((r for r in a_ops[1:] if r != off_reg), a_ops[1])
            return ArrayShape(base_reg=base_reg, index_reg=index_reg or _reg(s_ops[1]),
                              elem_bytes=elem_bytes, shift_form=True, bound_reg=bound_reg)
        base_reg = a_ops[1] if a_ops[2] == index_reg else a_ops[2]
        return ArrayShape(base_reg=base_reg, index_reg=index_reg or a_ops[2],
                          elem_bytes=elem_bytes, shift_form=False, bound_reg=bound_reg)

    def _reg_init_before_loop(self, header: int, reg: str) -> tuple[str, int] | None:
        """The (source-reg, addr) of the latest `mv reg, src` / `addi reg, src, 0` before the loop —
        where a strength-reduced pointer gets its starting (base) value."""
        best: tuple[str, int] | None = None
        for ins in sorted((i for b in self.blocks.values() for i in b.insns if i.addr < header),
                          key=lambda i: i.addr):
            ops = [o.strip() for o in ins.operands.split(",")]
            if not ops or _reg(ops[0]) != reg:
                continue
            if ins.mnemonic in ("mv", "c.mv") and len(ops) == 2:
                best = (_reg(ops[1]), ins.addr)
            elif ins.mnemonic in ("addi", "c.addi") and len(ops) >= 3 and _imm(ops[2]) == 0:
                best = (_reg(ops[1]), ins.addr)
        return best

    def loop_mem_ops(self, header: int) -> set[str]:
        """Memory mnemonics that occur inside the loop body (loads/stores)."""
        loop = self._natural_loop(header)
        return {i.mnemonic for s in loop for i in self.blocks[s].insns} & (_LOADS | _STORES)

    def data_dependent_exit(self, header: int) -> bool:
        """True when the loop's exit is data-dependent (a search): more than one way out, and the
        body loads from memory (the compared value comes from the data). Fix #3: case-split."""
        return len({d for _, d in self.loop_exit_edges(header)}) > 1 and bool(self.loop_mem_ops(header) & _LOADS)

    def aliased_stores(self, header: int) -> bool:
        """True when the loop body stores through a pointer (needs noverlap reasoning). Fix #4."""
        return bool(self.loop_mem_ops(header) & _STORES)

    def _natural_loop(self, header: int) -> set[int]:
        """Block starts of the natural loop(s) of `header`: header plus every block that can reach
        a back-edge tail without passing through header. Pure CFG structure."""
        preds = self._preds()
        loop = {header}
        stack = [s for s, t in self.back_edges if t == header]
        while stack:
            n = stack.pop()
            if n not in loop:
                loop.add(n)
                stack.extend(preds.get(n, []))
        return loop

    def search_loop_timing(self, header: int) -> "SearchTiming | None":
        """Derive the DISJUNCTIVE timing of a data-dependent search loop (GAP 2). The found and
        not-found cycle forms share setup + n*body + shutdown and differ only in the PARTIAL last
        iteration, which the loop's two exit edges take. Structure:

            setup + (Some i => i | None => trip)*body + (Some => found_partial | None => notfound_partial) + shutdown

        The discriminator is which exit edge is structural vs data-dependent: the BOUND branch
        (index/pointer vs limit, recovered by GAP 1) is the not-found exit; the OTHER exiting branch
        is the data-dependent match, the found exit. Each partial is the cost of the iteration prefix
        up to its exit branch, that branch costed at whichever direction (taken/fallthrough) LEAVES
        the loop. Returns None unless the loop is a clean 2-exit search (bound test + match test).

        Validated to reproduce the vendored `time_of_find_in_array` term-for-term (the oracle)."""
        if header not in self.loop_headers:
            return None
        loop = self._natural_loop(header)
        loop_insns = sorted((i for s in loop for i in self.blocks[s].insns if i.addr >= header),
                            key=lambda i: i.addr)
        # exit branches: a conditional branch with EITHER edge leaving the loop (taken OR fallthrough)
        exits = [ins for ins in loop_insns if ins.mnemonic in _BRANCH_OPS
                 and (_branch_target(ins.operands) not in loop or self._falls_out(ins, loop))]
        if len(exits) != 2:
            return None
        shape = self.array_search_shape(header)
        if shape is None:
            return None
        lb = self._loop_bound(header, shape.index_reg)
        if lb is None:
            return None
        bound = next((b for b in exits if b.addr == lb[1]), None)
        match = next((b for b in exits if b.addr != lb[1]), None)
        if bound is None or match is None:
            return None

        setup, body = self.loop_timing(header)
        found = self._partial_to(loop_insns, match, loop)
        notfound = self._partial_to(loop_insns, bound, loop)
        shutdown = self._shutdown_terms(header)
        return SearchTiming(setup=setup, body=body, found_partial=found,
                            notfound_partial=notfound, shutdown=shutdown)

    def _falls_out(self, branch: "Insn", loop: set[int]) -> bool:
        """True if the branch's FALLTHROUGH successor leaves the loop (so it exits when not taken)."""
        blk = next((b for b in self.blocks.values() if any(i.addr == branch.addr for i in b.insns)),
                   None)
        if blk is None:
            return False
        taken = _branch_target(branch.operands)
        return any(s is not None and s != taken and s not in loop for s in blk.succ)

    def _partial_to(self, loop_insns: list, exit_branch: "Insn", loop: set[int]) -> str:
        """Cost of the iteration prefix from the header up to `exit_branch`, with in-loop branches
        costed at their stay-in-loop edge and `exit_branch` costed at the edge that LEAVES the loop."""
        terms: list[str] = []
        for ins in loop_insns:
            if ins.addr > exit_branch.addr:
                break
            if ins.addr == exit_branch.addr:
                exit_taken = _branch_target(ins.operands) not in loop   # exits by taking it?
                term = _insn_tconst(ins, exit_taken)
                if term:
                    terms.append(term)
                break
            taken = (_branch_target(ins.operands) in loop) if ins.mnemonic in _BRANCH_OPS else None
            term = _insn_tconst(ins, taken)
            if term:
                terms.append(term)
        return " + ".join(terms)

    def _shutdown_terms(self, header: int) -> str:
        """Post-loop straight-line cost: from the loop's (single) exit target up to and including the
        return — the `shutdown` term of the closed form."""
        dsts = {d for _, d in self.loop_exit_edges(header)}
        if len(dsts) != 1:
            return ""
        tgt = next(iter(dsts))
        terms: list[str] = []
        for ins in sorted((i for b in self.blocks.values() for i in b.insns if i.addr >= tgt),
                          key=lambda i: i.addr):
            term = _insn_tconst(ins, False if ins.mnemonic in _BRANCH_OPS else None)
            if term:
                terms.append(term)
            if ins.mnemonic in ("jalr", "ret"):
                break
        return " + ".join(terms)

    def loop_timing(self, header: int) -> tuple[str, str] | None:
        """Derive (prefix, body) Coq timing expressions for the loop at `header`, summed from the
        per-instruction constants — so the loop arm's `cycle = prefix + counter * body` is exact by
        construction, never guessed. `prefix` = straight-line instructions before the loop
        (entry..header); `body` = one iteration (the loop blocks' instructions, branches resolved to
        the in-loop edge). Returns None if `header` is not a loop header. Address order = the order
        the gold invariants use, so the strings match the vendored proofs exactly."""
        if header not in self.loop_headers:
            return None
        loop = self._natural_loop(header)

        body: list[str] = []
        loop_insns = sorted((i for s in loop for i in self.blocks[s].insns), key=lambda i: i.addr)
        for ins in loop_insns:
            taken = None
            if ins.mnemonic in _BRANCH_OPS:
                tgt = _branch_target(ins.operands)
                taken = tgt in loop  # staying in the loop by TAKING the branch?
            term = _insn_tconst(ins, taken)
            if term:
                body.append(term)

        prefix: list[str] = []
        all_insns = sorted((i for b in self.blocks.values() for i in b.insns), key=lambda i: i.addr)
        for ins in all_insns:
            if self.entry <= ins.addr < header:
                taken = None
                if ins.mnemonic in _BRANCH_OPS:
                    taken = _branch_target(ins.operands) in loop
                term = _insn_tconst(ins, taken)
                if term:
                    prefix.append(term)

        return " + ".join(prefix), " + ".join(body)

    def _loop_arm_hint(self, a: int) -> str:
        """The synthesis hint for a loop-header hole, specialised to the loop's shape
        (fixes #2 array/pointer, #3 search, #4 aliasing). The CFG-computed loop timing is given as
        authoritative guidance; the structural scaffold (exists-index / case-split / noverlap) tells
        the model which shape the discharge needs, instead of one generic FILL_ME."""
        tim = self.loop_timing(a)
        if tim is None:
            return (f"(* HOLE:0x{a:x} loop invariant: facts that hold here, plus "
                    f"cycle_count_of_trace t' = closed form *) FILL_ME")
        prefix, body = tim
        rhs = f"{prefix} + i * ({body})" if prefix else f"i * ({body})"
        iv = self.induction_var(a)
        parts = [f"(* HOLE:0x{a:x} loop invariant. Per-iteration body time is FIXED: ({body}); "
                 f"pre-loop time: ({prefix or '0'})."]
        if iv:
            reg, step = iv
            parts.append(f"FIX #2 (array/pointer): introduce an index `exists i, i <= len /\\ "
                         f"(s {reg}) = base + i * {step} /\\ cycle_count_of_trace t' = {rhs}`; the "
                         f"discharge instantiates the witness i := ((s {reg}) - base) / {step}.")
        else:
            parts.append(f"use `exists i, i <= len /\\ cycle_count_of_trace t' = {rhs}`.")
        if self.data_dependent_exit(a):
            shape = self.array_search_shape(a)
            if shape is not None:
                addr = shape.addr_expr("i")
                parts.append(
                    f"FIX #3 (search/early-exit): the exit is data-dependent. The element access is "
                    f"`mem {shape.load_notation}[arr + ({addr})]` (base {shape.base_reg}, index "
                    f"{shape.index_reg}); the exit arm's postcondition is the found/not-found "
                    f"DISJUNCTION over `key_in_array`, and the discharge case-splits with "
                    f"`destruct (key_in_array_dec (s' V_MEM32) arr key len) as [IN | NOT_IN]` "
                    f"(both the predicate and its decidability are emitted from this shape).")
            else:
                parts.append("FIX #3 (search/early-exit): the exit is data-dependent — case-split "
                             "with `decide` on the loaded predicate; the bound is a `<=` over len.")
        if self.aliased_stores(a):
            parts.append("FIX #4 (aliasing): the body stores through a pointer — carry "
                         "`noverlaps`/`getmem_noverlap` side-conditions for the written addresses.")
        return " ".join(parts) + " *) FILL_ME"

    def skeleton_plan(self, spec) -> SkeletonPlan:
        """Build the invariant skeleton for `spec`. Requires `spec.postcondition` (the pinned
        exit arm); raises a clear error if it is missing rather than guessing the claim."""
        post = getattr(spec, "postcondition", None)
        if not post:
            raise ValueError(
                "skeleton synthesis requires a pinned exit arm: set `postcondition` on the "
                f"target spec for '{getattr(spec, 'name', '?')}'"
            )
        params = list(spec.params)
        loops = set(self.loop_headers)
        joins = set(self.join_points())
        exits = self.exit_points()
        # entry + loop headers + control-flow joins are holes (the model's job); exits stay pinned.
        hole_addrs = [a for a in dict.fromkeys([self.entry, *self.loop_headers, *self.join_points()])
                      if a not in exits]

        prompt_arms: list[tuple[int, str]] = []
        for a in hole_addrs:
            if a in loops:
                # The loop timing is COMPUTED from the CFG (sum of per-instruction constants), so
                # the model never has to guess it. We give it as authoritative GUIDANCE but leave the
                # arm free-form (a single FILL_ME) so the model can still shape the conjuncts/`exists`
                # to whatever the discharge needs — forcing a rigid template breaks proof reuse.
                hint = self._loop_arm_hint(a)
            elif a in joins:
                hint = (f"(* HOLE:0x{a:x} branch-join invariant: the register/memory facts that hold "
                        f"here, plus cycle_count_of_trace t' = closed form *) FILL_ME")
            else:
                # The entry precondition is almost always just cycle_count = 0; extra register ties
                # must be PROVABLE from the theorem's entry hypotheses, so over-specifying (e.g. a
                # value for a callee-clobbered register) makes the base case unprovable.
                hint = (f"(* HOLE:0x{a:x} entry precondition: normally exactly "
                        f"`cycle_count_of_trace t' = 0`. Add a register tie ONLY if a later arm "
                        f"needs it, and only one given as an entry hypothesis *) FILL_ME")
            prompt_arms.append((a, hint))
        for a in exits:
            prompt_arms.append((
                a,
                f"{post} (* PINNED:0x{a:x} postcondition from spec — do not change *)",
            ))
        prompt_text = _render_definition("timing_invs", params, prompt_arms)

        return SkeletonPlan(
            inv_name="timing_invs",
            params=params,
            hole_addrs=hole_addrs,
            exit_addrs=exits,
            postcondition=post,
            prompt_text=prompt_text,
        )

    def invariant_skeleton(self, spec) -> str:
        """The Coq invariant skeleton string (holes for entry/loops, pinned exit arm)."""
        return self.skeleton_plan(spec).prompt_text

    def describe(self) -> str:
        """A compact textual CFG for the LLM prompt."""
        lines = [f"entry: 0x{self.entry:x}", f"loop headers: {[hex(h) for h in self.loop_headers]}"]
        for start in sorted(self.blocks):
            b = self.blocks[start]
            body = "; ".join(f"{i.mnemonic} {i.operands}".strip() for i in b.insns)
            lines.append(f"block 0x{start:x} -> {[hex(s) for s in b.succ]} : {body}")
        return "\n".join(lines)


def _render_definition(inv_name: str, params: list[tuple[str, ...]], arms: list[tuple[int, str]]) -> str:
    """Render a `Definition <name> (<binders>) (t:trace) := match ... end.` from address->body arms.

    The match scaffold (`(Addr a, s) :: t'`, the `| _ => None` fall-throughs) is fixed here so it
    is identical between the prompt skeleton and the filled invariant — the addresses and structure
    are ours, never the model's. Arms are emitted in ascending address order.
    """
    binders = " ".join(f"({p[0]} : {p[1]})" for p in params)
    binders = f"{binders} " if binders else ""
    arm_lines = "\n".join(f"| 0x{a:x} => Some ({body})" for a, body in sorted(arms))
    return (
        f"Definition {inv_name} {binders}(t:trace) :=\n"
        f"match t with (Addr a, s) :: t' => match a with\n"
        f"{arm_lines}\n"
        f"| _ => None\n"
        f"end | _ => None end."
    )


def parse_objdump(listing: str) -> list[Insn]:
    insns: list[Insn] = []
    for line in listing.splitlines():
        m = _INSN.match(line)
        if m:
            insns.append(Insn(int(m.group(1), 16), m.group(2), m.group(3).strip()))
    return insns


def _branch_target(operands: str) -> int | None:
    m = _TARGET.search(operands)
    return int(m.group(1), 16) if m else None


def build_cfg(insns: list[Insn]) -> CFG:
    if not insns:
        return CFG(blocks={}, entry=0, back_edges=[])
    addrs = [i.addr for i in insns]
    leaders = {addrs[0]}
    by_addr = {i.addr: i for i in insns}

    # leaders: branch targets and instructions following a branch, plus terminal returns.
    for idx, ins in enumerate(insns):
        if ins.mnemonic in _BRANCH:
            tgt = _branch_target(ins.operands)
            if tgt is not None and tgt in by_addr:
                leaders.add(tgt)
            if idx + 1 < len(insns):
                leaders.add(insns[idx + 1].addr)
        # A return (ret/jalr) is the program's exit address. Make it a leader so it starts its
        # own terminal (no-successor) block; otherwise a straight-line function is a single block
        # whose "exit" would be reported as the entry address (no separate exit point).
        if ins.mnemonic in ("ret", "jalr"):
            leaders.add(ins.addr)

    leaders_sorted = sorted(leaders)
    blocks: dict[int, Block] = {ld: Block(start=ld) for ld in leaders_sorted}

    cur = leaders_sorted[0]
    for ins in insns:
        if ins.addr in blocks and ins.addr != cur:
            cur = ins.addr
        blocks[cur].insns.append(ins)

    # successors
    next_leader = {ld: (leaders_sorted[i + 1] if i + 1 < len(leaders_sorted) else None)
                   for i, ld in enumerate(leaders_sorted)}
    for start, b in blocks.items():
        if not b.insns:
            continue
        last = b.insns[-1]
        tgt = _branch_target(last.operands) if last.mnemonic in _BRANCH else None
        if last.mnemonic in ("ret", "jalr"):
            continue
        if last.mnemonic in ("j", "jal") and tgt is not None:
            b.succ = [tgt]
        elif last.mnemonic in _BRANCH and tgt is not None:
            fall = next_leader[start]
            b.succ = [tgt] + ([fall] if fall is not None else [])
        else:
            fall = next_leader[start]
            b.succ = [fall] if fall is not None else []

    # back-edges: successor address <= block start (a jump backwards)
    back_edges = [(s, t) for s, b in blocks.items() for t in b.succ if t is not None and t <= s]
    return CFG(blocks=blocks, entry=leaders_sorted[0], back_edges=back_edges)
