import { useRef, useState } from "react";

// Extensible MCU catalogue. Only NEORV32 is wired today; others are listed but disabled so the
// dropdown obviously extends without implying capability we don't have.
const MCUS = [
  { id: "neorv32", label: "NEORV32", note: "RISC-V rv32im_zicsr_zicntr", enabled: true },
  { id: "cortex-m", label: "ARM Cortex-M", note: "coming soon", enabled: false },
  { id: "rp2040", label: "RP2040", note: "coming soon", enabled: false },
];

// A `.c`/`.i` upload takes the compile front door (pinned riscv gcc); anything else is treated as
// a prebuilt ELF/object and disassembled directly. Mirrors api/service.py:C_SOURCE_SUFFIXES.
const C_SUFFIXES = [".c", ".i"];
const isCSource = (name: string) => C_SUFFIXES.some((s) => name.toLowerCase().endsWith(s));

export interface SubmitArgs {
  file: File;
  mcu: string;
  func?: string;
}

export function UploadForm({
  onSubmit,
  busy,
}: {
  onSubmit: (a: SubmitArgs) => void;
  busy: boolean;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [mcu, setMcu] = useState("neorv32");
  const [func, setFunc] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const cSource = file ? isCSource(file.name) : false;
  const defaultFunc = file ? file.name.replace(/\.[^.]+$/, "") : "";

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    onSubmit({ file, mcu, func: cSource ? func : undefined });
  }

  return (
    <form className="card form" onSubmit={submit}>
      <h2 className="card-title">Prove a C function or a machine-code program</h2>

      <label className="field-label" htmlFor="mcu">
        Microcontroller
      </label>
      <select id="mcu" className="select" value={mcu} onChange={(e) => setMcu(e.target.value)}>
        {MCUS.map((m) => (
          <option key={m.id} value={m.id} disabled={!m.enabled}>
            {m.label} — {m.note}
          </option>
        ))}
      </select>

      <label className="field-label">Source or machine code</label>
      <div
        className={`dropzone ${dragOver ? "dropzone-over" : ""} ${file ? "dropzone-has" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          setFile(e.dataTransfer.files?.[0] ?? null);
        }}
        role="button"
        tabIndex={0}
      >
        <input
          ref={inputRef}
          type="file"
          hidden
          accept=".c,.i,.o,.elf,.out,application/octet-stream"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        {file ? (
          <span className="dropzone-file">
            <strong>{file.name}</strong> <span className="muted">({file.size} bytes)</span>
            <span className={`chip ${cSource ? "chip-ok" : "chip-running"}`}>
              {cSource ? "C source → compile" : "machine code"}
            </span>
          </span>
        ) : (
          <span className="dropzone-hint">
            Drop a <code>.c</code> source or a RISC-V <code>ELF</code> / <code>.o</code> here, or
            click to choose
          </span>
        )}
      </div>
      <p className="hint">
        A <code>.c</code> file is compiled with the pinned RISC-V GCC
        (<code>-march=rv32im_zicsr_zicntr -mabi=ilp32 -O2</code>) and lifted; a prebuilt
        ELF/object is disassembled directly — no source, no compiler step.
      </p>

      {cSource && (
        <>
          <label className="field-label" htmlFor="func">
            Function name <span className="muted">(C source — the symbol to prove)</span>
          </label>
          <input
            id="func"
            className="select"
            type="text"
            value={func}
            placeholder={defaultFunc}
            onChange={(e) => setFunc(e.target.value)}
          />
          <p className="hint">
            Defaults to the file name (<code>{defaultFunc || "stem"}</code>). Set this if the
            function differs from the file name.
          </p>
        </>
      )}

      <button className="btn btn-primary" type="submit" disabled={!file || busy}>
        {busy ? "Proving…" : cSource ? "Compile & prove" : "Lift & prove"}
      </button>
    </form>
  );
}
