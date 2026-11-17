"""Reproducible figures for the brief. Run from the repo root:  python docs/brief/make_figures.py"""
import json
from datetime import date
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from analytics_copilot.db.database import Database
from analytics_copilot.db.schema import create_and_seed
from analytics_copilot.evals.bank import load_bank
from analytics_copilot.viz import render_chart

FIG = Path("docs/brief/figures"); FIG.mkdir(parents=True, exist_ok=True)
ACC = "#1f4e9e"; LIGHT = "#eef3fb"; GREY = "#555555"; RED = "#b23a3a"

def box(ax, x, y, w, h, text, fc=LIGHT, ec=ACC, fs=8.5, bold=False, tc="#1a1a1a"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc=fc, ec=ec, lw=1.4))
    ax.text(x+w/2, y+h/2, text, ha="center", va="center", fontsize=fs, color=tc, fontweight="bold" if bold else "normal")
def arrow(ax, p, q, c=ACC, ls="-"):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=13, color=c, lw=1.5, linestyle=ls))

# 1. architecture
fig, ax = plt.subplots(figsize=(9, 4.4)); ax.set_xlim(0, 12); ax.set_ylim(0, 6); ax.axis("off")
box(ax, 0.1, 4.2, 1.7, 1.0, "Business\nquestion", fc="white")
box(ax, 2.3, 4.2, 2.0, 1.0, "Schema RAG\n(keyword retriever)")
box(ax, 4.8, 4.2, 2.1, 1.0, "LLM: text-to-SQL\n(structured output,\nconfidence)")
box(ax, 7.4, 4.2, 2.0, 1.0, "SQL validator\nparse | SELECT-only |\ntable allow-list", fc=ACC, tc="white", bold=True, fs=8)
box(ax, 9.9, 4.2, 2.0, 1.0, "Executor\n+ auto LIMIT")
for a, b in [(1.8, 2.3), (4.3, 4.8), (6.9, 7.4), (9.4, 9.9)]: arrow(ax, (a, 4.7), (b, 4.7))
box(ax, 9.9, 2.3, 2.0, 1.0, "Backend protocol\nDuckDB | Databricks", fc="#f6f6f6", ec=GREY)
arrow(ax, (10.9, 4.2), (10.9, 3.3))
box(ax, 9.9, 0.4, 2.0, 1.0, "Result rows")
arrow(ax, (10.9, 2.3), (10.9, 1.4))
box(ax, 6.9, 0.4, 2.3, 1.0, "viz.py\nchart from result shape")
box(ax, 4.0, 0.4, 2.4, 1.0, "LLM: summarizer\n(executive prose)")
box(ax, 0.3, 0.4, 3.0, 1.0, "AnswerResult:\nSQL + table + chart + summary", fc="white", bold=True)
arrow(ax, (9.9, 0.9), (9.2, 0.9)); arrow(ax, (6.9, 0.9), (6.4, 0.9)); arrow(ax, (4.0, 0.9), (3.3, 0.9))
box(ax, 7.4, 2.5, 2.0, 0.8, "Rejected: error,\nnothing executed", fc="#fbeaea", ec=RED, fs=8, tc=RED)
arrow(ax, (8.4, 4.2), (8.4, 3.3), c=RED, ls="--")
ax.text(4.2, 3.0, "The LLM never touches the database.\nThe validator is the trusted boundary.", fontsize=9.5, color=ACC, ha="center", style="italic")
box(ax, 1.0, 2.2, 5.4, 0.0001, "", fc="white", ec="white")
fig.tight_layout(); fig.savefig(FIG/"architecture.png", dpi=200); plt.close(fig)

# 2. query bank composition
bank = load_bank()
from collections import Counter
cats = Counter(q.category for q in bank.questions)
order = sorted(cats, key=lambda k: -cats[k])
fig, ax = plt.subplots(figsize=(5.2, 3.0))
bars = ax.barh(order[::-1], [cats[k] for k in order[::-1]], color=ACC)
for b in bars: ax.text(b.get_width()+0.15, b.get_y()+b.get_height()/2, int(b.get_width()), va="center", fontsize=9)
ax.set_xlabel("Questions"); ax.set_title(f"Held-out query bank v{bank.version}: {len(bank.questions)} questions", fontsize=10)
for s in ("top", "right"): ax.spines[s].set_visible(False)
fig.tight_layout(); fig.savefig(FIG/"bank_composition.png", dpi=200); plt.close(fig)

# 3. chart via viz.py on real demo query results
db = Database(); db.connect()
create_and_seed(db.connection, seed=42, today=date.fromisoformat(str(bank.anchor_date)))
by_id = {q.id: q for q in bank.questions}
for qid, name in [("monthly-revenue-2025", "viz_line.png"), ("top-categories-revenue", "viz_bar.png")]:
    q = by_id[qid]; r = db.query(q.gold_sql)
    p = render_chart(r.columns, r.rows, FIG/name, title=q.question)
    print(qid, r.columns, r.row_count, p)

# 4. validator outcomes
cases = [("SELECT category, COUNT(*) FROM products GROUP BY category", True),
         ("DROP TABLE orders", False), ("DELETE FROM orders WHERE 1=1", False),
         ("UPDATE orders SET status='x'", False), ("SELECT * FROM secrets", False),
         ("SELECT * FROM pg_catalog.pg_user", False), ("SELECT 1", False),
         ("SELECT * FROM customers; DROP TABLE customers", False)]
from analytics_copilot.sql.validator import SQLValidator, SQLValidationError
v = SQLValidator()
fig, ax = plt.subplots(figsize=(7.2, 3.0)); ax.axis("off"); ax.set_xlim(0, 10); ax.set_ylim(0, len(cases)+0.6)
ax.text(0.1, len(cases)+0.1, "Input SQL", fontweight="bold", fontsize=8.5); ax.text(6.4, len(cases)+0.1, "Validator outcome", fontweight="bold", fontsize=8.5)
for i, (s, _) in enumerate(cases):
    y = len(cases)-1-i+0.1
    try:
        res = v.validate(s); msg = "ALLOWED (LIMIT 1000 added)" if res.row_limit_added else "ALLOWED"; col = "#1b7a3a"
    except SQLValidationError as e:
        m = str(e); msg = "BLOCKED: " + (m.split(".")[0].replace("Only SELECT statements are allowed; got", "not a SELECT, got").replace("Reference to disallowed table:", "disallowed table")[:48]); col = RED
    ax.text(0.1, y, s if len(s) < 62 else s[:59]+"...", family="monospace", fontsize=7.2)
    ax.text(6.4, y, msg, fontsize=7.6, color=col)
fig.tight_layout(); fig.savefig(FIG/"validator.png", dpi=200); plt.close(fig)
