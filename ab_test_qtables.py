import os
import csv
import json
import random
import statistics
from datetime import datetime
from agents.agent_rl_learning import QTable
from collections import Counter

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
CURRENT = os.path.join(MODEL_DIR, 'rl_qtable.json')
# find latest backup if any
BACKUP_DIR = os.path.join(MODEL_DIR, 'backups')
backup_files = []
if os.path.isdir(BACKUP_DIR):
    backup_files = sorted([os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.endswith('.json')])

if not os.path.exists(CURRENT):
    print('No current Q-table found at', CURRENT); raise SystemExit(1)

old_path = backup_files[-1] if backup_files else None
if not old_path:
    print('No backup Q-table found to compare. Create one first (backups/).')
    raise SystemExit(1)

print('Comparing:')
print('  current:', CURRENT)
print('  previous:', old_path)

q_new = QTable()
q_old = QTable()
q_new.load(CURRENT)
q_old.load(old_path)

journal = os.path.join(os.path.dirname(__file__), 'logs', 'trade_journal.csv')
rows = []
with open(journal, newline='') as f:
    rdr = csv.DictReader(f)
    for r in rdr:
        if r.get('result','').upper() in ('WIN','LOSS') and r.get('state_key'):
            rows.append(r)

# use last N trades
N = 200
rows = rows[-N:]

metrics = {'new': Counter(), 'old': Counter()}
for r in rows:
    state = r['state_key']
    sig = r.get('signal','BUY').upper()
    actual_action = 0 if sig=='BUY' else 1
    result = r.get('result','').upper()

    a_new, conf_new, _ = q_new.best_action(state)
    a_old, conf_old, _ = q_old.best_action(state)

    # record whether the Q would have chosen the action that was taken and whether that action won
    metrics['new']['total'] += 1
    metrics['old']['total'] += 1
    if a_new == actual_action:
        metrics['new']['matched_action'] += 1
        if result == 'WIN':
            metrics['new']['matched_and_win'] += 1
    else:
        if result == 'WIN':
            metrics['new']['mismatch_but_win'] += 1

    if a_old == actual_action:
        metrics['old']['matched_action'] += 1
        if result == 'WIN':
            metrics['old']['matched_and_win'] += 1
    else:
        if result == 'WIN':
            metrics['old']['mismatch_but_win'] += 1

# Print summary
for k in ('old','new'):
    m = metrics[k]
    tot = m['total'] or 1
    print(f"\n=== {k.upper()} Q-table ===")
    print(f"Total evaluated: {tot}")
    print(f"Matched action count: {m['matched_action']} ({m['matched_action']/tot:.1%})")
    print(f"Matched action that resulted in win: {m['matched_and_win']} ({m['matched_and_win']/tot:.1%})")
    print(f"Wins when action mismatched: {m['mismatch_but_win']} ({m['mismatch_but_win']/tot:.1%})")

# Simple comparative metric: proportion of wins correctly predicted
new_score = (metrics['new']['matched_and_win'] / (metrics['new']['matched_action'] or 1))
old_score = (metrics['old']['matched_and_win'] / (metrics['old']['matched_action'] or 1))
print('\nCOMPARISON:')
print(f'New Q-table correctness on matched actions: {new_score:.2%}')
print(f'Old Q-table correctness on matched actions: {old_score:.2%}')

if new_score > old_score:
    print('\n=> New Q-table performs BETTER on matched actions')
else:
    print('\n=> Old Q-table performs BETTER or equal on matched actions')

# ===== Bootstrapped significance test =====
def bootstrap_metric(rows, q, n_iter=1000):
    vals = []
    for _ in range(n_iter):
        sample = [random.choice(rows) for _ in range(len(rows))]
        matched_and_win = 0
        matched_action = 0
        for r in sample:
            state = r['state_key']
            sig = r.get('signal','BUY').upper()
            actual_action = 0 if sig=='BUY' else 1
            a,_,_ = q.best_action(state)
            if a == actual_action:
                matched_action += 1
                if r.get('result','').upper() == 'WIN':
                    matched_and_win += 1
        vals.append((matched_and_win / (matched_action or 1)))
    return vals

boot_new = bootstrap_metric(rows, q_new, n_iter=500)
boot_old = bootstrap_metric(rows, q_old, n_iter=500)
mean_new = statistics.mean(boot_new)
mean_old = statistics.mean(boot_old)
diff = mean_new - mean_old
pv = sum(1 for a,b in zip(boot_new, boot_old) if a <= b) / len(boot_new)

report = {
    'total_evaluated': len(rows),
    'new_score': new_score,
    'old_score': old_score,
    'bootstrap': {
        'mean_new': mean_new,
        'mean_old': mean_old,
        'diff': diff,
        'p_value_estimate': pv,
    }
}

outdir = os.path.join(os.path.dirname(__file__), 'reports')
os.makedirs(outdir, exist_ok=True)
ofile = os.path.join(outdir, f'abtest_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
with open(ofile, 'w') as f:
    json.dump(report, f, indent=2)
print(f'Bootstrap report saved: {ofile}')
