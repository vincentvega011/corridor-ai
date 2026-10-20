"use client";

import "@xyflow/react/dist/style.css";
import { Background, Controls, Position, ReactFlow, type Edge, type Node } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import type { ProposalTraceResponse } from "@/lib/contracts";
import { formatMinor } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

/** What buildTrace needs to write the trace in the UI language: console strings and the officer time format. */
export interface TraceCtx { tc: (key: string, params?: Record<string, string | number>) => string; time: (iso: string) => string }

type Entry = NonNullable<ProposalTraceResponse["trace_entries"]>[number];
type Tone = "neutral" | "untrusted" | "deny" | "mfa" | "ok";
/** Where a step runs: what the customer typed, an untrusted document, a model, or the deterministic core. */
export type Zone = "input" | "untrusted" | "model" | "core";

const text = (v: unknown) => (v === null || v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v));

/** Build the path of one proposal from the ledger: message, document, parser, proposal, every rule, risk, grant, MFA, outcome. */
export function buildTrace(t: ProposalTraceResponse, ctx: TraceCtx): { nodes: Node[]; edges: Edge[]; steps: { id: string; title: string; tone: Tone; lines: string[]; zone: Zone; raw?: string; at?: string; rule?: { id: string; result: string } }[] } {
  const { tc, time: formatOfficerTime } = ctx;
  const entries: Entry[] = (t.trace_entries ?? []).length ? (t.trace_entries ?? []) : (t.entries ?? []);
  const of = (type: string) => entries.filter((e) => e.event_type === type);
  const steps: { id: string; title: string; tone: Tone; lines: string[]; zone: Zone; raw?: string; at?: string; rule?: { id: string; result: string } }[] = [];
  const proposal = (t.proposal ?? {}) as Record<string, unknown>;
  const docs = ((proposal.derived_from_docs as string[] | undefined) ?? []).length > 0 || of("reader_extraction").some((e) => e.actor === "agent");

  // a button flow (ADR 0021) records its origin in the cascade decision: parser "structured", reason "button"
  const button = of("cascade_decision").some((e) => e.payload.parser === "structured");
  if (!button) steps.push({ id: "msg", title: tc("c.tr.msg"), tone: "neutral", zone: "input", lines: [tc("c.tr.msgBody")] });
  for (const e of of("reader_extraction")) {
    const p = e.payload;
    steps.push({
      id: `doc${e.seq}`, at: e.ts, title: tc("c.tr.doc"), tone: "untrusted", zone: "untrusted",
      lines: [tc("c.tr.kind", { v: text(p.kind) }), tc("c.tr.inj", { v: text(p.injection_suspected) }), tc("c.tr.signals", { v: text(p.signals) }), tc("c.tr.docBody")],
    });
  }
  for (const e of button ? [] : of("cascade_decision")) {
    const p = e.payload;
    steps.push({ id: `parse${e.seq}`, at: e.ts, title: tc("c.tr.parse"), tone: "neutral", zone: "model", lines: [tc("c.tr.intent", { intent: text(p.intent), lang: text(p.language) }), tc("c.tr.parser", { parser: text(p.parser), c: text(p.cascaded), bps: text(p.confidence_bps) }), tc("c.tr.slots", { v: text(p.slots) })], ...(p.parser_output_hash ? { raw: `${t.parser_outputs?.[String(p.parser_output_hash)] ?? "expired"}` } : {}) });
  }
  const prov = (proposal.provenance ?? {}) as Record<string, unknown>;
  if (button) {
    const slots = (of("cascade_decision")[0]?.payload.slots ?? {}) as Record<string, unknown>;
    steps.push({
      id: "button", title: tc("c.tr.button"), tone: "neutral", zone: "core",
      lines: [tc("c.tr.action", { v: text(proposal.action) }), ...(proposal.amount_minor ? [tc("c.tr.amount", { v: formatMinor(proposal.amount_minor, proposal.currency) })] : []), ...(proposal.payee_id ? [tc("c.tr.payee", { v: text(proposal.payee_id) })] : []), tc("c.tr.typedInputs", { v: Object.entries(slots).map(([k, v]) => `${k} (${text(v)})`).join(", ") || tc("c.tr.none") }), tc("c.tr.buttonBody")],
    });
  } else
  steps.push({
    id: "prop", title: tc("c.tr.prop"), tone: docs ? "untrusted" : "neutral", zone: "model",
    lines: [tc("c.tr.action", { v: text(proposal.action) }), ...(proposal.amount_minor ? [tc("c.tr.amountOrigin", { v: formatMinor(proposal.amount_minor, proposal.currency), o: text(prov.amount) })] : []), ...(proposal.payee_id ? [tc("c.tr.payeeOrigin", { v: text(proposal.payee_id), o: text(prov.payee) })] : []), docs ? tc("c.tr.fromDoc") : tc("c.tr.fromUser")],
  });
  const d = (t.decisions ?? [])[0];
  const ORDER = ["deny", "require_mfa", "require_confirm", "allow"];
  const byRule = new Map<string, { result: string; reasons: string[] }>();
  for (const [rule, result, reason] of (d?.rule_results ?? []) as string[][]) {
    const cur = byRule.get(String(rule)) ?? { result: String(result), reasons: [] };
    if (ORDER.indexOf(String(result)) < ORDER.indexOf(cur.result)) cur.result = String(result);
    cur.reasons.push(String(reason));
    byRule.set(String(rule), cur); // one node per rule id: the most restrictive result, every reason listed
  }
  for (const [rule, r] of byRule) {
    steps.push({ id: `rule-${rule}`, title: tc("c.tr.rule", { id: rule }), zone: "core", rule: { id: rule, result: r.result }, tone: r.result === "deny" ? "deny" : r.result === "require_mfa" ? "mfa" : "neutral", lines: [tc("c.tr.result", { v: r.result }), ...r.reasons.map((x) => tc("c.tr.reason", { v: x }))] });
  }
  if (d?.risk) steps.push({ id: "risk", zone: "core", title: tc("c.tr.risk", { level: d.risk.level, bps: d.risk.score_bps }), tone: d.risk.level === "critical" ? "deny" : d.risk.level === "high" ? "mfa" : "neutral", lines: d.risk.reasons.map((r) => `${r.code}: +${r.contribution_bps}`) });
  if (d) steps.push({ id: "decision", zone: "core", title: tc("c.tr.decision", { d: d.decision }), tone: d.decision === "deny" ? "deny" : d.decision === "require_mfa" ? "mfa" : "ok", lines: [tc("c.tr.rules", { v: d.rule_ids.join(", ") }), tc("c.tr.policyVer", { v: d.policy_version_hash.slice(0, 12) })] });
  for (const e of of("mfa_attempt")) steps.push({ id: `mfa${e.seq}`, at: e.ts, zone: "core", title: tc("c.tr.mfa"), tone: e.payload.ok ? "ok" : "deny", lines: [e.payload.ok ? tc("c.tr.mfaOk") : tc("c.tr.mfaNo")] });
  for (const e of of("confirmation_granted")) steps.push({ id: `grant${e.seq}`, at: e.ts, zone: "core", title: tc("c.tr.grant"), tone: "ok", lines: [tc("c.tr.level", { v: text(e.payload.level) }), tc("c.tr.grantBody")] });
  for (const e of of("execution")) steps.push({ id: `exec${e.seq}`, at: e.ts, zone: "core", title: tc("c.tr.exec"), tone: "ok", lines: [tc("c.tr.action", { v: text(e.payload.action) }), ...(e.payload.hold_until ? [tc("c.tr.heldUntil", { t: formatOfficerTime(String(e.payload.hold_until)) })] : [])] });
  for (const e of [...of("rejection"), ...of("confirmation_rejected"), ...of("execution_failed")]) steps.push({ id: `rej${e.seq}`, at: e.ts, zone: "core", title: tc("c.tr.rej"), tone: "deny", lines: [`${e.event_type}: ${text(e.payload.reason ?? e.payload.refused ?? e.payload.action)}`] });
  if (!of("execution").length && !of("rejection").length && d?.decision === "deny") steps.push({ id: "denied", zone: "core", title: tc("c.tr.denied"), tone: "deny", lines: [tc("c.tr.noMoney")] });

  // left to right: input, model, deterministic core, outcome
  const stageOf = (x: (typeof steps)[number]): number =>
    x.zone === "input" || x.zone === "untrusted" ? 0 : x.id === "button" || x.zone === "model" ? 1 : x.id.startsWith("rule-") ? 2 : /^(risk|decision)/.test(x.id) ? 3 : 4;
  const row: number[] = [0, 0, 0, 0, 0];
  const nodes: Node[] = steps.map((s) => {
    const col = stageOf(s);
    const y = row[col]!;
    row[col] = y + 1;
    return {
      id: s.id,
      position: { x: col * 385, y: y * 200 },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      className: `rf-node rf-${s.zone} rf-tone-${s.tone}`,
      data: { label: (
        <div data-testid={`trace-node-${s.id.replace(/[^a-z0-9]/gi, "-")}`} data-tone={s.tone} data-zone={s.zone} className="text-left text-sm">
          <p className="rf-zone">{tc(`c.tr.zone.${s.zone}`)}</p>
          <p className="text-[0.9375rem] font-semibold">{s.title}</p>
          {s.rule ? <p className="my-1"><span className="rulechip" data-result={s.rule.result}>{s.rule.id}: {s.rule.result}</span></p> : null}
          {s.lines.map((l, j) => <p key={j} className="break-words">{l}</p>)}
          {s.at ? <p className="mono mt-1 text-xs text-muted">{formatOfficerTime(s.at)}</p> : null}
          {s.raw ? <details className="nodrag mt-1"><summary className="cursor-pointer font-semibold text-pine">{tc("c.tr.showParser")}</summary><pre className="mono mt-1 whitespace-pre-wrap break-all text-xs">{s.raw}</pre></details> : null}
        </div>
      ) },
      draggable: false,
    };
  });
  const ids = steps.map((x) => x.id);
  const has = (id: string) => ids.includes(id);
  const link: [string, string][] = [];
  const inputs = steps.filter((x) => stageOf(x) === 0).map((x) => x.id);
  const parses = steps.filter((x) => x.id.startsWith("parse")).map((x) => x.id);
  const lastParse = parses[parses.length - 1];
  const head = has("button") ? "button" : "prop";
  for (const i of inputs) link.push([i, lastParse ?? head]);
  parses.slice(0, -1).forEach((x) => link.push([x, lastParse!]));
  if (lastParse) link.push([lastParse, head]);
  const rules = steps.filter((x) => x.id.startsWith("rule-")).map((x) => x.id);
  for (const r of rules) { link.push([head, r]); if (has("decision")) link.push([r, "decision"]); }
  if (has("risk")) { link.push([head, "risk"]); if (has("decision")) link.push(["risk", "decision"]); }
  const outs = steps.filter((x) => stageOf(x) === 4).map((x) => x.id);
  outs.forEach((o, k) => link.push([k === 0 ? (has("decision") ? "decision" : head) : outs[k - 1]!, o]));
  const edges: Edge[] = link.filter(([a, b]) => has(a) && has(b) && a !== b).map(([a, b], i) => ({ id: `e${i}`, source: a, target: b, type: "smoothstep", animated: false }));
  return { nodes, edges, steps };
}

export function ZoneLegend() {
  const { tc } = useI18n();
  return (
    <ul className="flex flex-wrap items-center gap-3 text-xs" aria-label={tc("c.tr.legend")}>
      {(["untrusted", "model", "core"] as Zone[]).map((z) => (
        <li key={z} className={`rf-legend rf-${z}`}><span className="rf-swatch" />{tc(`c.tr.zone.${z}`)}</li>
      ))}
    </ul>
  );
}

const COL_W = 385; // column step of the layout (a node is 350 wide)
const ROW_H = 200; // row step
const NODE_H = 175; // a tall node, so the last row is not clipped

export default function TraceGraph({ trace }: { trace: ProposalTraceResponse }) {
  const { tc, fmtOfficerTime } = useI18n();
  const { nodes, edges } = buildTrace(trace, { tc, time: fmtOfficerTime });
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1200);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  // fit the content to the width, from the top left: the canvas is exactly as tall as the content (no empty band)
  const cols = new Set(nodes.map((n) => n.position.x)).size;
  const rows = Math.max(1, ...Object.values(nodes.reduce<Record<number, number>>((m, n) => ({ ...m, [n.position.x]: (m[n.position.x] ?? 0) + 1 }), {})));
  const contentW = (cols - 1) * COL_W + 350;
  const zoom = Math.min(1, Math.max(0.4, (width - 24) / contentW));
  const height = Math.max(200, Math.round(((rows - 1) * ROW_H + NODE_H) * zoom + 24));
  return (
    <div ref={box} className="w-full rounded-lg border border-line bg-surface" style={{ height }} data-testid="trace-graph">
      <ReactFlow key={`${Math.round(zoom * 100)}-${height}`} nodes={nodes} edges={edges} defaultViewport={{ x: 12, y: 12, zoom }} minZoom={0.3} nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} proOptions={{ hideAttribution: true }}>
        <Background color="var(--color-line)" gap={20} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
