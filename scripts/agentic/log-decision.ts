// Append one entry to logs/decisions/<agent>.jsonl. Refuses entries containing secrets or emails.
// node scripts/agentic/log-decision.ts --agent implementer --ticket T-007 --stage build \
//   --decision "..." --why "..." [--alternatives "a|b"] [--commit sha] [--model sonnet] [--parent orchestrator]
import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { root } from "./lib/budget.ts";
import { findSensitive } from "./lib/safety.ts";

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : undefined;
}

const agent = arg("agent");
const decision = arg("decision");
const why = arg("why");
if (!agent || !decision || !why) {
  process.stderr.write("log-decision: --agent, --decision and --why are required\n");
  process.exit(1);
}
if (!/^[a-z][a-z0-9-]*$/.test(agent)) {
  process.stderr.write("log-decision: --agent must be a lowercase slug\n");
  process.exit(1);
}

const entry = {
  ts: new Date().toISOString(),
  agent,
  parent: arg("parent") ?? (agent === "orchestrator" ? "human" : "orchestrator"),
  session: arg("session") ?? process.env.CLAUDE_SESSION_ID ?? "unknown",
  ticket: arg("ticket") ?? "none",
  stage: arg("stage") ?? "unknown",
  model: arg("model") ?? "unknown",
  decision,
  why,
  alternatives: (arg("alternatives") ?? "").split("|").filter(Boolean),
  commit: arg("commit") ?? "",
};

const line = JSON.stringify(entry);
const hit = findSensitive(line);
if (hit) {
  process.stderr.write(
    `log-decision: refused, entry looks like it contains a ${hit}. Rewrite without it.\n`,
  );
  process.exit(1);
}

const dir = join(root(), "logs", "decisions");
mkdirSync(dir, { recursive: true });
appendFileSync(join(dir, `${agent}.jsonl`), line + "\n");
