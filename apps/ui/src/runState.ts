import { useEffect, useState } from "react";

export const terminal = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

export function shortId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-5)}`;
}

export /**
 * A clock that ticks only while something on screen is still running.
 *
 * A live run's elapsed time has to advance on its own — the run list polls every 2.5s,
 * but `HistoryPage` does not poll at all, so without this a live row there would show a
 * duration frozen at first render. The interval is created only when `active`, so a page
 * of finished runs installs no timer and the common case costs nothing.
 */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  // Up to one second stale on the tick that first activates the timer, which a duration
  // label cannot show. Resynchronising on activation would mean setting state inside the
  // effect, and a cascading render is a worse trade than a second of lag.
  return now;
}
