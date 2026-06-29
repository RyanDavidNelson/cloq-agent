"""Unit tests for the compile stage. The actual gcc invocation is validated in the toolchain
image; here we test the pinned flags and the clean-failure behaviour when the toolchain is absent.
"""
from __future__ import annotations

import shutil

import pytest

from cloq_agent.lift import compile as cc


def test_pinned_flags_match_the_timing_model():
    # The matched-pair rule: non-compressed rv32im, ilp32, -O2 (CLAUDE.md golden rule #4).
    assert cc.DEFAULT_CFLAGS == (
        "-march=rv32im_zicsr_zicntr", "-mabi=ilp32", "-O2", "-ffreestanding", "-nostdlib",
    )
    assert cc.OPT_LEVEL == "-O2"
    assert "c" not in cc.MARCH.split("_")[0]  # no compressed extension


def test_missing_c_file_is_a_clean_failure(tmp_path):
    r = cc.compile_c(tmp_path / "nope.c", "f")
    assert not r.ok
    assert r.error and "no such C file" in r.error


def test_missing_toolchain_is_a_clean_failure(tmp_path):
    c = tmp_path / "f.c"
    c.write_text("unsigned f(unsigned x){return x;}\n")
    r = cc.compile_c(c, "f", gcc="definitely-not-a-real-gcc-xyz")
    assert not r.ok
    assert r.error and "not on PATH" in r.error


@pytest.mark.skipif(shutil.which(cc.GCC) is None, reason="RISC-V toolchain not installed")
def test_compile_straight_line_when_toolchain_present(tmp_path):
    c = tmp_path / "sum3.c"
    c.write_text("unsigned sum3(unsigned a,unsigned b,unsigned cc){return a+b+cc;}\n")
    r = cc.compile_c(c, "sum3", workdir=tmp_path)
    assert r.ok, r.error
    assert r.objdump and "<sum3>:" in r.objdump
    assert "c." not in r.objdump  # non-compressed codegen


def test_sanitize_ident():
    assert cc.sanitize_ident("sum3") == "sum3"
    assert cc.sanitize_ident("my-prog.elf") == "my_prog_elf"
    assert cc.sanitize_ident("123go")[0] == "p"   # leading digit prefixed
    assert cc.sanitize_ident("") == "program"


def test_load_machine_code_missing_file(tmp_path):
    r = cc.load_machine_code(tmp_path / "nope.o")
    assert not r.ok and r.error and "no such file" in r.error
    assert r.func == "nope"           # name derived from the file stem
    assert r.flags == []              # no compiler flags in the machine-code intake


def test_load_machine_code_rejects_non_object(tmp_path):
    f = tmp_path / "prog.o"
    f.write_text("definitely not an ELF\n")
    r = cc.load_machine_code(f)
    assert not r.ok                  # objdump-absent OR not-an-object -> clean failure, no raise
    assert r.error


@pytest.mark.skipif(shutil.which(cc.GCC) is None or shutil.which(cc.OBJDUMP) is None,
                    reason="RISC-V toolchain not installed")
def test_load_machine_code_disassembles_real_object(tmp_path):
    c = tmp_path / "sum3.c"
    c.write_text("unsigned sum3(unsigned a,unsigned b,unsigned cc){return a+b+cc;}\n")
    obj = tmp_path / "sum3.o"
    cc.compile_c(c, "sum3", workdir=tmp_path)
    r = cc.load_machine_code(obj)
    assert r.ok, r.error
    assert r.objdump and "<sum3>:" in r.objdump
    assert r.flags == []
