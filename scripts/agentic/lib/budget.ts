// Pure budget math + log readers shared by guard-budget, log-usage and budget-report.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

export type Size = "S" | "M" | "L";
export type Status = "ok" | "warn" | "stop";

export interface Budgets {
  warn_ratio: number;
  stop_ratio: number;
  ticket_usd: Record<Size, number>;
  run: { usd: number; turns: number };
  day_usd: number;
  week_usd: number;
  unusual_run_multiple: number;
  prices_usd_per_mtok: Record<
    string,
    { in: number; out: number; cache_read: number; cache_write: number }
  >;
}

export interface UsageEntry {
  ts: string;
  agent: string;
  parent: string;
  session: string;
  ticket: string;
  stage: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  cost_usd: number;
  tool_calls: number;
  turns: number;
}

export interface Check {
  scope: string;
  status: Status;
  spent: number;
  cap: number;
  ratio: number;
}

export const root = (): string => process.env.KIT_ROOT ?? process.cwd();

export function loadBudgets(dir = root()): Budgets {
  return JSON.parse(readFileSync(join(dir, ".claude", "budgets.json"), "utf8")) as Budgets;
}

export function classify(
  spent: number,
  cap: number,
  b: Pick<Budgets, "warn_ratio" | "stop_ratio">,
): Status {
  if (cap <= 0) return "stop";
  const ratio = spent / cap;
  if (ratio >= b.stop_ratio) return "stop";
  if (ratio >= b.warn_ratio) return "warn";
  return "ok";
}

export function check(scope: string, spent: number, cap: number, b: Budgets): Check {
  return {
    scope,
    status: classify(spent, cap, b),
    spent,
    cap,
    ratio: cap > 0 ? spent / cap : Infinity,
  };
}

export function modelFamily(model: string): "sonnet" | "haiku" | "opus" {
  const m = model.toLowerCase();
  if (m.includes("haiku")) return "haiku";
  if (m.includes("opus")) return "opus";
  return "sonnet";
}

export function costUsd(
  model: string,
  t: { tokens_in: number; tokens_out: number; cache_read: number; cache_write: number },
  b: Budgets,
): number {
  const p = b.prices_usd_per_mtok[modelFamily(model)];
  if (!p) return 0;
  const usd =
    (t.tokens_in * p.in +
      t.tokens_out * p.out +
      t.cache_read * p.cache_read +
      t.cache_write * p.cache_write) /
    1e6;
  return Math.round(usd * 1e6) / 1e6;
}

export function readUsage(dir = root()): UsageEntry[] {
  const d = join(dir, "logs", "usage");
  if (!existsSync(d)) return [];
  const out: UsageEntry[] = [];
  for (const f of readdirSync(d).filter((x) => x.endsWith(".jsonl"))) {
    for (const line of readFileSync(join(d, f), "utf8").split("\n")) {
      if (line.trim()) out.push(JSON.parse(line) as UsageEntry);
    }
  }
  return out;
}

export const sum = (es: UsageEntry[]): number => es.reduce((a, e) => a + e.cost_usd, 0);

export function dayKey(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** ISO week key like 2026-W42 (UTC). */
export function weekKey(d: Date): string {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const y0 = Date.UTC(t.getUTCFullYear(), 0, 1);
  const wk = Math.ceil(((t.getTime() - y0) / 86400000 + 1) / 7);
  return `${t.getUTCFullYear()}-W${String(wk).padStart(2, "0")}`;
}

export function spendToday(es: UsageEntry[], now = new Date()): number {
  const k = dayKey(now);
  return sum(es.filter((e) => e.ts.slice(0, 10) === k));
}

export function spendThisWeek(es: UsageEntry[], now = new Date()): number {
  const k = weekKey(now);
  return sum(es.filter((e) => weekKey(new Date(e.ts)) === k));
}

export function spendForTicket(es: UsageEntry[], ticket: string): number {
  return sum(es.filter((e) => e.ticket === ticket));
}

/** Read `size:` from docs/tickets/<ticket>.md; default M when absent. */
export function ticketSize(ticket: string, dir = root()): Size {
  const f = join(dir, "docs", "tickets", `${ticket}.md`);
  if (!existsSync(f)) return "M";
  const m = /^size:\s*([SML])\b/m.exec(readFileSync(f, "utf8"));
  return (m?.[1] as Size | undefined) ?? "M";
}

export function median(xs: number[]): number {
  if (xs.length === 0) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? (s[mid] as number) : ((s[mid - 1] as number) + (s[mid] as number)) / 2;
}

/** A run is unusual when above N times the median cost of earlier runs of the same stage. */
export function isUnusual(cost: number, stage: string, es: UsageEntry[], b: Budgets): boolean {
  const prior = es.filter((e) => e.stage === stage && e.cost_usd > 0).map((e) => e.cost_usd);
  if (prior.length < 3) return false;
  return cost > b.unusual_run_multiple * median(prior);
}

/** Every scope that applies before delegating work for `ticket`. */
export function checkAll(ticket: string | null, now = new Date(), dir = root()): Check[] {
  const b = loadBudgets(dir);
  const es = readUsage(dir);
  const checks: Check[] = [
    check("day", spendToday(es, now), b.day_usd, b),
    check("week", spendThisWeek(es, now), b.week_usd, b),
  ];
  if (ticket) {
    checks.push(
      check(
        `ticket ${ticket}`,
        spendForTicket(es, ticket),
        b.ticket_usd[ticketSize(ticket, dir)],
        b,
      ),
    );
  }
  return checks;
}
