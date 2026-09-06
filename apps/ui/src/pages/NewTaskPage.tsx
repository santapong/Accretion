import { FormEvent, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { lines } from "./formLines";
import { PlanningReview } from "./PlanningReview";
import type { Project, TaskCreate, TaskPlanning } from "../types";

function ProjectCreator({ onCreated }: { onCreated: (project: Project) => void }) {
  const [status, setStatus] = useState<string>();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setStatus("Creating project…");
    try {
      const project = await api.createProject({
        name: String(data.get("name")),
        repository_path: String(data.get("repository_path")),
      });
      form.reset();
      onCreated(project);
      setStatus(`Created ${project.name}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Project creation failed.");
    }
  }

  return (
    <form className="project-form" onSubmit={submit}>
      <label>Project name<input name="name" required placeholder="Accretion" /></label>
      <label>Local repository path<input name="repository_path" required placeholder="/workspace/repository" /></label>
      <button className="secondary-button" type="submit">Add project</button>
      {status ? <p className="form-status" role="status">{status}</p> : null}
    </form>
  );
}

function NewTaskForm({ projects, onPlanning }: {
  projects: Project[];
  onPlanning: (planning: TaskPlanning) => void;
}) {
  const [projectId, setProjectId] = useState("");
  const [status, setStatus] = useState<string>();
  const selectedProjectId = projectId || projects[0]?.project_id || "";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const requiredOutputs = lines(data.get("required_outputs")).map((path) => ({
      path,
      kind: "file",
      non_empty: true,
    }));
    const payload: TaskCreate = {
      project_id: String(data.get("project_id")),
      objective: String(data.get("objective")),
      task_type: String(data.get("task_type")) as TaskCreate["task_type"],
      risk_level: String(data.get("risk_level")) as TaskCreate["risk_level"],
      constraints: lines(data.get("constraints")),
      success_criteria: lines(data.get("success_criteria")),
      allowed_capabilities: lines(data.get("allowed_capabilities")),
      denied_capabilities: lines(data.get("denied_capabilities")),
      required_outputs: requiredOutputs,
      budgets: {
        wall_time_seconds: Number(data.get("wall_time_seconds")),
        max_turns: Number(data.get("max_turns")),
        max_tool_calls: Number(data.get("max_tool_calls")),
        max_loop_iterations: Number(data.get("max_loop_iterations")),
        max_parallel_runs: Number(data.get("max_parallel_runs")),
      },
    };
    setStatus("Profiling task…");
    try {
      const task = await api.createTask(payload);
      const planning = await api.planning(task.envelope.task_id);
      onPlanning(planning);
      setStatus("Task created. Review the deterministic plan below.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Task creation failed.");
    }
  }

  return (
    <form className="task-form" onSubmit={submit}>
      <label className="field-wide">Objective<textarea name="objective" required rows={3} placeholder="Describe the outcome without routing instructions." /></label>
      <label>Project<select name="project_id" required value={selectedProjectId} onChange={(event) => setProjectId(event.target.value)}><option value="">Select a project</option>{projects.map((project) => <option value={project.project_id} key={project.project_id}>{project.name}</option>)}</select></label>
      <label>Task type<select name="task_type" defaultValue="OTHER"><option>RESEARCH</option><option>ANALYSIS</option><option>IMPLEMENT</option><option>REVIEW</option><option>EXPERIMENT</option><option>OTHER</option></select></label>
      <label>Risk<select name="risk_level" defaultValue="LOW"><option>LOW</option><option>MEDIUM</option><option>HIGH</option><option>CRITICAL</option></select></label>
      <label>Wall time (seconds)<input name="wall_time_seconds" type="number" min="1" defaultValue="1800" /></label>
      <label>Max turns<input name="max_turns" type="number" min="1" defaultValue="20" /></label>
      <label>Max tool calls<input name="max_tool_calls" type="number" min="1" defaultValue="100" /></label>
      <label>Loop iterations<input name="max_loop_iterations" type="number" min="1" defaultValue="1" /></label>
      <label>Parallel runs<input name="max_parallel_runs" type="number" min="1" defaultValue="1" /></label>
      <label>Constraints <small>one per line</small><textarea name="constraints" rows={4} /></label>
      <label>Success criteria <small>one per line</small><textarea name="success_criteria" rows={4} /></label>
      <label>Allowed capabilities <small>one per line</small><textarea name="allowed_capabilities" rows={4} /></label>
      <label>Denied capabilities <small>one per line</small><textarea name="denied_capabilities" rows={4} /></label>
      <label className="field-wide">Required output paths <small>one repository-relative file path per line</small><textarea name="required_outputs" rows={3} placeholder={"reports/result.json\nsrc/generated-summary.md"} /></label>
      <div className="form-actions field-wide"><button className="primary-button" disabled={!projects.length} type="submit">Create and profile task</button>{status ? <p className="form-status" role="status">{status}</p> : null}</div>
    </form>
  );
}

export function NewTaskPage() {
  const queryClient = useQueryClient();
  const projectQuery = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const [planning, setPlanning] = useState<TaskPlanning>();
  return (
    <>
      <section className="task-studio page-panel">
        <header className="section-heading"><div><p className="eyebrow">Create and review</p><h1>New task</h1></div><span>deterministic-profiler-v1</span></header>
        <ProjectCreator onCreated={(project) => { queryClient.setQueryData<Project[]>(["projects"], (current = []) => [...current, project]); }} />
        <NewTaskForm projects={projectQuery.data ?? []} onPlanning={setPlanning} />
      </section>
      {planning ? <PlanningReview planning={planning} onUpdate={setPlanning} /> : null}
    </>
  );
}
