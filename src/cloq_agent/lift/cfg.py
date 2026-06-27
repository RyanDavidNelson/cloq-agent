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
class CFG:
    blocks: dict[int, Block]
    entry: int
    back_edges: list[tuple[int, int]]   # (from_block_start, to_block_start) == loop headers

    @property
    def loop_headers(self) -> list[int]:
        return sorted({to for _, to in self.back_edges})

    def describe(self) -> str:
        """A compact textual CFG for the LLM prompt."""
        lines = [f"entry: 0x{self.entry:x}", f"loop headers: {[hex(h) for h in self.loop_headers]}"]
        for start in sorted(self.blocks):
            b = self.blocks[start]
            body = "; ".join(f"{i.mnemonic} {i.operands}".strip() for i in b.insns)
            lines.append(f"block 0x{start:x} -> {[hex(s) for s in b.succ]} : {body}")
        return "\n".join(lines)


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
