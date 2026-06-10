import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "motion/react";

const stages = [
  { cmd: "rm ~/.cache/agentcairn/index.duckdb", out: "index deleted.", label: "Delete the index" },
  { cmd: "cairn reindex ~/vault", out: "rebuilding from Markdown… 128 notes indexed.", label: "Reindex" },
  { cmd: "cairn recall \"auth fix\"", out: "restored — 0 facts lost. The vault was the truth.", label: "Recall" },
];

export default function SurvivesUninstallDemo() {
  const reduce = useReducedMotion();
  const [advanced, setAdvanced] = useState(0);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  // Derive the visible index reactively: under reduced motion always show the
  // final state (this corrects even if useReducedMotion() resolves after mount,
  // rather than getting stuck at the seed value). Otherwise advance via the button.
  const i = reduce ? stages.length - 1 : advanced;
  const shown = stages.slice(0, i + 1);
  // SSR/first paint/reduced motion render visible (no hidden initial); stages added
  // by clicking after mount animate in.
  const motionOK = mounted && !reduce;
  return (
    <div data-testid="uninstall-demo" className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 font-mono text-[13px]">
      {shown.map((s, k) => (
        <motion.div key={k}
          initial={motionOK ? { opacity: 0, y: 8 } : false}
          animate={{ opacity: 1, y: 0 }} transition={{ duration: motionOK ? 0.25 : 0 }} className="mb-2">
          <div><span className="text-[var(--color-accent)]">$</span> {s.cmd}</div>
          <div className="text-[var(--color-ink-muted)]">{s.out}</div>
        </motion.div>
      ))}
      {i < stages.length - 1 && (
        <button onClick={() => setAdvanced((n) => n + 1)}
          className="mt-2 font-sans text-[13px] font-medium text-[var(--color-accent)] hover:underline">
          {stages[i + 1].label} →
        </button>
      )}
    </div>
  );
}
