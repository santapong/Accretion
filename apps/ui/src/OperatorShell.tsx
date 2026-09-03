/**
 * The application shell: the persistent navigation bar and the router outlet.
 *
 * Both read `ROUTES` from `./routes`, which is what makes the nav and the router one
 * list rather than two.
 *
 * `end` is derived here as `route.path === "/"` rather than stored per row. Under
 * react-router 7 it is belt and braces: that version's prefix match also requires the
 * next character to be a separator, so `to="/"` is never marked current on
 * `/tasks/new` even without it. It is kept because it is what the pre-M9 shell said, it
 * costs nothing, and it stays correct under a router that drops that guard. Stored per
 * row it would be wrong for fourteen of fifteen rows with nothing able to tell.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { ROUTES } from "./routes";

export function OperatorShell() {
  const meQuery = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const me = meQuery.data;
  const identity = me?.principal
    ? `${me.principal.display_name ?? me.principal.subject}${me.memberships?.[0] ? " · " + me.memberships[0].role : ""}`
    : "Control plane";
  return (
    <main>
      <nav>
        <Link className="brand-link" to="/"><span className="brand-mark">A</span><span><strong>Accretion</strong><small>Operator / v{__APP_VERSION__}</small></span></Link>
        <div className="nav-links">{ROUTES.filter((route) => route.label).map((route) => <NavLink end={route.path === "/"} key={route.path} to={route.path}>{route.label}</NavLink>)}</div>
        <div className="nav-status"><i />{identity}</div>
      </nav>
      <div className="shell">
        <Routes>
          {ROUTES.map((route) => <Route key={route.path} path={route.path} element={route.element} />)}
        </Routes>
      </div>
    </main>
  );
}
