import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";

export function CapabilitiesPage() {
  const capabilities = useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities });
  const skills = useQuery({ queryKey: ["skills"], queryFn: api.skills });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: api.plugins });
  return (
    <section className="page-panel">
      <header className="section-heading"><div><p className="eyebrow">Read-only registry</p><h1>Capabilities, skills, and plugins</h1></div></header>
      <p className="page-status">A capability listed here may still be unusable. <Link to="/admin/capabilities/inspect">Inspect a capability</Link> to see the binding and connection a run would really use.</p>
      <div className="registry-grid">
        <article className="registry-card"><h2>Capabilities</h2><ul className="registry-list">{(capabilities.data ?? []).map((item) => <li key={`${item.capability_id}:${item.version}`}><strong>{item.capability_id}@{item.version}</strong><span>{item.kind} · {item.risk} · {item.backend}</span></li>)}</ul></article>
        <article className="registry-card"><h2>Skills</h2><ul className="registry-list">{(skills.data ?? []).map((item) => <li key={`${item.skill_id}:${item.version}`}><strong>{item.skill_id}@{item.version}</strong><span>{(item.required_capabilities ?? []).join(", ")}</span></li>)}</ul></article>
        <article className="registry-card"><h2>Plugins</h2><ul className="registry-list">{(plugins.data ?? []).map((item) => <li key={`${item.plugin_id}:${item.version}`}><strong>{item.plugin_id}@{item.version}</strong><span>{(item.skill_refs ?? []).join(", ")}</span></li>)}</ul></article>
      </div>
    </section>
  );
}
