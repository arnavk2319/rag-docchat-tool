// SubagentStop / Stop hook: append token use and cost to logs/usage/<agent>.jsonl and the ticket.
// Reads hook JSON on stdin. Logs only the delta since the last call for the same transcript.
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";
import {
  costUsd,
  isUnusual,
  loadBudgets,
  readUsage,
  root,
  spendForTicket,
  type UsageEntry,
} from "./lib/budget.ts";

interface Totals {
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  cost_usd: number;
  turns: number;
  tool_calls: number;
}

const ZERO: Totals = {
  tokens_in: 0,
  tokens_out: 0,
  cache_read: 0,
  cache_write: 0,
  cost_usd: 0,
  turns: 0,
  tool_calls: 0,
};
const STAGES: Record<string, string> = {
  planner: "plan",
  architect: "design",
  implementer: "build",
  "test-writer": "test",
  reviewer: "review",
  evaluator: "eval",
  "security-auditor": "security",
  deployer: "deploy",
  "ops-watcher": "operate",
  orchestrator: "orchestrate",
};

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : undefined;
}

function readPayload(): Record<string, unknown> {
  if (process.stdin.isTTY) return {};
  try {
    return JSON.parse(readFileSync(0, "utf8")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/** Sum usage across a transcript, counting each assistant message id once. */
export function totalsFromTranscript(path: string): { totals: Totals; model: string } {
  const b = loadBudgets();
  const seen = new Set<string>();
  const t = { ...ZERO };
  let model = "unknown";
  for (const line of readFileSync(path, "utf8").split("\n")) {
    if (!line.trim()) continue;
    let o: {
      type?: string;
      message?: { id?: string; model?: string; usage?: Record<string, number>; content?: unknown };
    };
    try {
      o = JSON.parse(line);
    } catch {
      continue;
    }
    const m = o.message;
    if (o.type !== "assistant" || !m?.usage) continue;
    const id = m.id ?? `${seen.size}`;
    if (Array.isArray(m.content)) {
      t.tool_calls += m.content.filter((c: { type?: string }) => c.type === "tool_use").length;
    }
    if (seen.has(id)) continue;
    seen.add(id);
    model = m.model ?? model;
    const u = {
      tokens_in: m.usage.input_tokens ?? 0,
      tokens_out: m.usage.output_tokens ?? 0,
      cache_read: m.usage.cache_read_input_tokens ?? 0,
      cache_write: m.usage.cache_creation_input_tokens ?? 0,
    };
    t.tokens_in += u.tokens_in;
    t.tokens_out += u.tokens_out;
    t.cache_read += u.cache_read;
    t.cache_write += u.cache_write;
    t.cost_usd += costUsd(model, u, b);
    t.turns += 1;
  }
  return { totals: t, model };
}

/** First ticket id (T-123) named in the transcript's opening user message(s), i.e. the delegation brief. */
export function ticketFromTranscript(path: string): string | null {
  const lines = readFileSync(path, "utf8").split("\n");
  for (const line of lines.slice(0, 40)) {
    if (!line.trim()) continue;
    try {
      const o = JSON.parse(line) as { type?: string; message?: { content?: unknown } };
      if (o.type !== "user") continue;
      const m = /\bT-\d{3}\b/.exec(JSON.stringify(o.message?.content ?? ""));
      if (m) return m[0];
    } catch {
      // skip non-JSON lines
    }
  }
  return null;
}

/**
 * Which ticket a run's spend belongs to. Explicit --ticket / KIT_TICKET wins. A subagent's brief names its
 * ticket. The orchestrator session spans many tickets and the checked-out branch says nothing about which
 * one it is working on, so it is never attributed from the branch (it stays "none").
 */
function currentTicket(agent: string, transcript: string): string {
  const a = arg("ticket") ?? process.env.KIT_TICKET;
  if (a) return a;
  if (agent === "orchestrator") return "none";
  return ticketFromTranscript(transcript) ?? "none";
}

function bumpTicket(ticket: string): void {
  const f = join(root(), "docs", "tickets", `${ticket}.md`);
  if (ticket === "none" || !existsSync(f)) return;
  const spent = spendForTicket(readUsage(), ticket).toFixed(2);
  const txt = readFileSync(f, "utf8");
  writeFileSync(f, txt.replace(/^spent_usd:.*$/m, `spent_usd: ${spent}`));
}

function main(): void {
  const p = readPayload();
  const transcript =
    arg("transcript") ??
    (p.agent_transcript_path as string | undefined) ??
    (p.transcript_path as string | undefined);
  if (!transcript || !existsSync(transcript)) return;

  const event = String(p.hook_event_name ?? arg("event") ?? "Stop");
  const agent =
    arg("agent") ??
    (event === "SubagentStop" ? String(p.agent_type ?? "subagent") : "orchestrator");
  const session = String(p.session_id ?? arg("session") ?? "unknown");

  const stateDir = join(root(), "logs", "runs", ".state");
  mkdirSync(stateDir, { recursive: true });
  const stateFile = join(
    stateDir,
    createHash("sha1").update(transcript).digest("hex").slice(0, 16) + ".json",
  );
  const prev: Totals = existsSync(stateFile)
    ? (JSON.parse(readFileSync(stateFile, "utf8")) as Totals)
    : { ...ZERO };

  const { totals, model } = totalsFromTranscript(transcript);
  const d: Totals = {
    tokens_in: totals.tokens_in - prev.tokens_in,
    tokens_out: totals.tokens_out - prev.tokens_out,
    cache_read: totals.cache_read - prev.cache_read,
    cache_write: totals.cache_write - prev.cache_write,
    cost_usd: Math.round((totals.cost_usd - prev.cost_usd) * 1e6) / 1e6,
    turns: totals.turns - prev.turns,
    tool_calls: totals.tool_calls - prev.tool_calls,
  };
  writeFileSync(stateFile, JSON.stringify(totals));
  if (d.turns <= 0) return;

  const ticket = currentTicket(agent, transcript);
  const stage = arg("stage") ?? STAGES[agent] ?? "unknown";
  const all = readUsage();
  const entry: UsageEntry & { unusual?: boolean } = {
    ts: new Date().toISOString(),
    agent,
    parent: agent === "orchestrator" ? "human" : "orchestrator",
    session,
    ticket,
    stage,
    model,
    tokens_in: d.tokens_in,
    tokens_out: d.tokens_out,
    cache_read: d.cache_read,
    cache_write: d.cache_write,
    cost_usd: d.cost_usd,
    tool_calls: d.tool_calls,
    turns: d.turns,
  };
  if (isUnusual(d.cost_usd, stage, all, loadBudgets())) entry.unusual = true;

  const dir = join(root(), "logs", "usage");
  mkdirSync(dir, { recursive: true });
  appendFileSync(join(dir, `${agent}.jsonl`), JSON.stringify(entry) + "\n");
  bumpTicket(ticket);
}

main();
