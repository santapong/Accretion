import { Link } from "react-router-dom";

export function NotFoundPage() {
  return <section className="page-panel"><h1>Page not found</h1><Link to="/">Return to dashboard</Link></section>;
}
