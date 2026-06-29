import { useRef, useState } from "react";

// Extensible MCU catalogue. Only NEORV32 is wired today; others are listed but disabled so the
// dropdown obviously extends without implying capability we don't have.
const MCUS = [
  { id: "neorv32", label: "NEORV32", note: "RISC-V rv32im_zicsr_zicntr", enabled: true },
  { id: "cortex-m", label: "ARM Cortex-M", note: "coming soon", enabled: false },
  { id: "rp2040", label: "RP2040", note: "coming soon", enabled: false },
];

export interface SubmitArgs {
  file: File;
  mcu: string;
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
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    onSubmit({ file, mcu });
  }

  return (
    <form className="card form" onSubmit={submit}>
      <h2 className="card-title">Prove a machine-code program</h2>

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

      <label className="field-label">Machine code</label>
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
        <input ref={inputRef} type="file" hidden onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        {file ? (
          <span className="dropzone-file">
            <strong>{file.name}</strong> <span className="muted">({file.size} bytes)</span>
          </span>
        ) : (
          <span className="dropzone-hint">
            Drop a RISC-V <code>ELF</code> / <code>.o</code> here, or click to choose
          </span>
        )}
      </div>
      <p className="hint">Any RISC-V machine-code artifact — no source, no compiler step.</p>

      <button className="btn btn-primary" type="submit" disabled={!file || busy}>
        {busy ? "Proving…" : "Lift & prove"}
      </button>
    </form>
  );
}
