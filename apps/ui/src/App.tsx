import { BrowserRouter } from "react-router-dom";
import { OperatorShell } from "./OperatorShell";
// The only stylesheet import, and it must stay AFTER `./OperatorShell`. M9 PR5c deleted
// `styles.css`; the invariant is now xyflow's unlayered sheet, then our unlayered canvas
// overrides beside it (`react-flow.css`, imported by `RunExecution.tsx` on the line after
// the xyflow one), then everything else in `@layer components` here. `./OperatorShell` is
// what reaches `RunExecution.tsx` and pulls the first two in; hoisting this line above it
// puts xyflow last and hands every same-specificity node rule back to it.
import "./theme.css";

export default function App() {
  return <BrowserRouter><OperatorShell /></BrowserRouter>;
}
