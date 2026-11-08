// PreToolUse hook (matcher Task|Agent) and CLI. Exit 2 at a cap, warn at 70%.
// Hook: tool call JSON on stdin. CLI: node scripts/agentic/guard-budget.ts --ticket T-001 [--now ISO]
import { readFileSync } from "node:fs";
import { checkAll, type Check } from "./lib/budget.ts";

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : undefined;
}

function stdinJson(): Record<string, unknown> {
  if (process.stdin.isTTY || arg("ticket")) return {};
  try {
    return JSON.parse(readFileSync(0, "utf8")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

const payload = stdinJson();
const input = (payload.tool_input ?? {}) as Record<string, unknown>;
const text = `${String(input.prompt ?? "")} ${String(input.description ?? "")}`;
const ticket = arg("ticket") ?? /\bT-\d{3}\b/.exec(text)?.[0] ?? null;
const now = arg("now") ? new Date(arg("now") as string) : new Date();

const checks = checkAll(ticket, now);
const fmt = (c: Check): string =>
  `${c.scope}: $${c.spent.toFixed(2)} of $${c.cap.toFixed(2)} (${Math.round(c.ratio * 100)}%)`;
const stops = checks.filter((c) => c.status === "stop");
const warns = checks.filter((c) => c.status === "warn");

if (stops.length > 0) {
  process.stderr.write(
    `BUDGET STOP: ${stops.map(fmt).join("; ")}. Write a budget report into the ticket ` +
      `(spend by agent/stage, what is left, forecast, options: raise cap, split ticket, downgrade model, ` +
      `drop scope) and ask the human. Do not delegate until they answer.\n`,
  );
  process.exit(2);
}

if (warns.length > 0) {
  const msg = `BUDGET WARN (70%+): ${warns.map(fmt).join("; ")}. Tell the human in one line and add it to docs/status.md.`;
  process.stdout.write(
    JSON.stringify({
      systemMessage: msg,
      hookSpecificOutput: { hookEventName: "PreToolUse", additionalContext: msg },
    }) + "\n",
  );
}
process.exit(0);
