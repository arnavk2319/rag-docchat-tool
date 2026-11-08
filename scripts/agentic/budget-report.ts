// pnpm budget: spend vs caps. Prints a compact table.
import {
  check,
  loadBudgets,
  readUsage,
  spendForTicket,
  spendThisWeek,
  spendToday,
  ticketSize,
  type Check,
} from "./lib/budget.ts";

const b = loadBudgets();
const es = readUsage();
const rows: Check[] = [
  check("today", spendToday(es), b.day_usd, b),
  check("this week", spendThisWeek(es), b.week_usd, b),
];
for (const t of [...new Set(es.map((e) => e.ticket))].filter((t) => t !== "none").sort()) {
  rows.push(check(`ticket ${t}`, spendForTicket(es, t), b.ticket_usd[ticketSize(t)], b));
}

const line = (r: Check): string =>
  `${r.scope.padEnd(16)} $${r.spent.toFixed(2).padStart(6)} / $${r.cap.toFixed(2).padStart(6)}  ${String(Math.round(r.ratio * 100)).padStart(4)}%  ${r.status.toUpperCase()}`;
console.log(rows.map(line).join("\n"));

const byAgent = new Map<string, number>();
for (const e of es) byAgent.set(e.agent, (byAgent.get(e.agent) ?? 0) + e.cost_usd);
if (byAgent.size > 0) {
  console.log("\nby agent");
  for (const [a, c] of [...byAgent].sort((x, y) => y[1] - x[1]))
    console.log(`  ${a.padEnd(14)} $${c.toFixed(2)}`);
}
const unusual = es.filter((e) => (e as { unusual?: boolean }).unusual);
if (unusual.length > 0) console.log(`\nflagged unusual runs: ${unusual.length}`);
if (es.length === 0) console.log("\nno usage logged yet");
