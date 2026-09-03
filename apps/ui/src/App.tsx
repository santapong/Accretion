import { BrowserRouter } from "react-router-dom";
import { OperatorShell } from "./OperatorShell";
// Tokens and the Tailwind layers first, then the legacy sheet. Order is documentation
// rather than cascade: styles.css is unlayered, so it wins either way. What is NOT
// documentation is that both sit AFTER `./OperatorShell`: that import reaches
// `RunExecution.tsx`, whose first import is React Flow's own stylesheet, and moving the
// two lines above it would put xyflow's rules last in the built cascade.
import "./theme.css";
import "./styles.css";

export default function App() {
  return <BrowserRouter><OperatorShell /></BrowserRouter>;
}
