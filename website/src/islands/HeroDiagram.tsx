import { motion, useReducedMotion } from "motion/react";

const ease = [0.16, 1, 0.3, 1] as const;

export default function HeroDiagram() {
  const reduce = useReducedMotion();
  const reveal = (delay: number) =>
    reduce
      ? { initial: { opacity: 1, y: 0 }, animate: { opacity: 1, y: 0 } }
      : { initial: { opacity: 0, y: 20 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.5, delay, ease } };
  const draw = (delay: number) =>
    reduce
      ? { initial: { pathLength: 1 }, animate: { pathLength: 1 } }
      : { initial: { pathLength: 0 }, animate: { pathLength: 1 }, transition: { duration: 0.8, delay, ease } };

  return (
    <div data-testid="hero-diagram" role="img" aria-label="Diagram: a Markdown note becomes a DuckDB index with a wikilink graph, then a cited recall result." className="mt-14 rounded-2xl border border-[var(--color-border)] p-7 grid grid-cols-1 md:grid-cols-[1fr_auto_1fr_auto_1fr] gap-4 items-center bg-[linear-gradient(180deg,#fff,#fcfcfb)]">
      <motion.div {...reveal(0)}>
        <p className="eyebrow mb-2 text-[10.5px]">Vault · source of truth</p>
        <pre className="font-mono text-[12px] leading-[1.55] bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-3.5 whitespace-pre-wrap">{`---
tags: [auth, fix]
---
Fixed login by rotating
`}<span className="text-[var(--color-accent)]">[[jwt-secret]]</span>{` during `}<span className="text-[var(--color-accent)]">[[deploy]]</span>.</pre>
      </motion.div>

      <svg width="40" height="20" className="mx-auto"><motion.path {...draw(0.4)} d="M2 10 H38" stroke="var(--color-ink-faint)" fill="none" /></svg>

      <motion.div {...reveal(0.5)} className="flex flex-col items-center gap-3">
        <p className="eyebrow text-[10.5px]">Index · disposable cache</p>
        <div className="font-mono text-[11px] text-[var(--color-ink-muted)] border border-[var(--color-border)] rounded-lg px-3.5 py-2.5 bg-white w-full text-center">DuckDB · vector + BM25</div>
        <div className="flex items-center gap-2">
          <motion.span {...reveal(0.8)} className="w-3.5 h-3.5 rounded-full bg-[var(--color-accent)]" />
          <svg width="26" height="2"><motion.line {...draw(0.9)} x1="0" y1="1" x2="26" y2="1" stroke="var(--color-border)" /></svg>
          <motion.span {...reveal(0.9)} className="w-3.5 h-3.5 rounded-full bg-[var(--color-accent-warm)]" />
        </div>
      </motion.div>

      <svg width="40" height="20" className="mx-auto"><motion.path {...draw(1.0)} d="M2 10 H38" stroke="var(--color-ink-faint)" fill="none" /></svg>

      <motion.div {...reveal(1.1)}>
        <p className="eyebrow mb-2 text-[10.5px]">MCP · recall</p>
        <div className="font-sans text-[13px] bg-white border border-[var(--color-border)] rounded-lg p-3.5">
          <p className="font-mono text-[11.5px] text-[var(--color-ink-muted)] mb-2">cairn recall "how did we fix login?"</p>
          <p className="leading-[1.45]">Rotated the jwt-secret during deploy.</p>
          <p className="font-mono text-[10.5px] text-[var(--color-accent)] mt-2">↳ auth-fix.md</p>
        </div>
      </motion.div>
    </div>
  );
}
