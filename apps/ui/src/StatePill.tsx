export function StatePill({ state }: { state: string }) {
  return <span className={`pill pill-${state.toLowerCase()}`}>{state.replaceAll("_", " ")}</span>;
}
