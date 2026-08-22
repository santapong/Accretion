import { useEffect, useState, type FormEvent } from "react";
import { X } from "lucide-react";
import type { ProviderHealth, ProviderName } from "../types";

interface Props {
  open: boolean;
  providers: ProviderHealth[];
  roots: string[];
  onClose: () => void;
  onSubmit: (input: { provider: ProviderName; cwd: string; prompt: string }) => Promise<void>;
}

export function NewSessionDialog({ open, providers, roots, onClose, onSubmit }: Props) {
  const [provider, setProvider] = useState<ProviderName>("codex");
  const [cwd, setCwd] = useState(roots[0] ?? "");
  const [prompt, setPrompt] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!cwd && roots[0]) setCwd(roots[0]);
  }, [cwd, roots]);

  if (!open) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    try {
      await onSubmit({ provider, cwd, prompt });
      setPrompt("");
      onClose();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-session-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog__header">
          <div>
            <p className="eyebrow">Managed run</p>
            <h2 id="new-session-title">Start a new session</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close dialog">
            <X size={18} />
          </button>
        </div>
        <form onSubmit={submit}>
          <label>
            Provider
            <div className="provider-picker">
              {(["codex", "claude"] as ProviderName[]).map((name) => {
                const health = providers.find((item) => item.name === name);
                return (
                  <button
                    className={`provider-choice ${provider === name ? "is-selected" : ""}`}
                    disabled={!health?.available}
                    key={name}
                    onClick={() => setProvider(name)}
                    type="button"
                  >
                    <span className={`provider-mark provider-mark--${name}`}>
                      {name === "codex" ? "CX" : "CL"}
                    </span>
                    <span>
                      <strong>{name === "codex" ? "Codex" : "Claude Code"}</strong>
                      <small>{health?.available ? "Ready" : "Unavailable"}</small>
                    </span>
                  </button>
                );
              })}
            </div>
          </label>
          <label>
            Workspace
            <input
              value={cwd}
              onChange={(event) => setCwd(event.target.value)}
              placeholder="/home/you/project"
              required
              list="workspace-roots"
            />
            <datalist id="workspace-roots">
              {roots.map((root) => (
                <option value={root} key={root} />
              ))}
            </datalist>
          </label>
          <label>
            First instruction
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="What should the agent work on?"
              rows={6}
              required
              autoFocus
            />
          </label>
          <div className="dialog__actions">
            <button className="button button--ghost" type="button" onClick={onClose}>
              Cancel
            </button>
            <button className="button button--primary" disabled={submitting} type="submit">
              {submitting ? "Starting…" : "Start session"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
