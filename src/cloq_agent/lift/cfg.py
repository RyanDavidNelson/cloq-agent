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
        """Terminal blocks (a `ret`/no-successor block) — the program's exit addresses."""
        return sorted(s for s, b in self.blocks.items() if not b.succ)

    def invariant_points(self) -> list[int]:
        """Ordered, de-duplicated invariant-point addresses, derived purely from the CFG:
        the entry, every loop header, and every exit. Never from the model."""
        return sorted({self.entry, *self.loop_headers, *self.exit_points()})

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
        exits = self.exit_points()
        # entry + loop headers are holes (the model's job); exits stay pinned.
        hole_addrs = [a for a in dict.fromkeys([self.entry, *self.loop_headers]) if a not in exits]

        prompt_arms: list[tuple[int, str]] = []
        for a in hole_addrs:
            kind = "loop invariant" if a in loops else "entry precondition"
            prompt_arms.append((
                a,
                f"(* HOLE:0x{a:x} {kind}: registers + cycle_count_of_trace t' = "
                f"closed form (c0 - c) * t_body + termination *) FILL_ME",
            ))
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

    # leaders: branch targets and instructions following a branch
    for idx, ins in enumerate(insns):
        if ins.mnemonic in _BRANCH:
            tgt = _branch_target(ins.operands)
            if tgt is not None and tgt in by_addr:
                leaders.add(tgt)
            if idx + 1 < len(insns):
                leaders.add(insns[idx + 1].addr)

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
