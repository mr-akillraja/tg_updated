import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime

# Set style
sns.set_style("darkgrid")
plt.rcParams['figure.figsize'] = (16, 12)
plt.rcParams['font.size'] = 9

# Read the trade journal
df = pd.read_csv('/home/akill-sud/Desktop/tg_algo_enhanced./tg_algo/logs/trade_journal.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])

# Create figure with subplots
fig = plt.figure(figsize=(20, 14))

# ════════════════════════════════════════════════════════════════════
# 1. RESULT BREAKDOWN PIE CHART
# ════════════════════════════════════════════════════════════════════
ax1 = plt.subplot(3, 4, 1)
result_counts = df['result'].value_counts()
# Build color palette and explode dynamically to match number of result categories
base_colors = ['#ff6b6b', '#51cf66', '#ffd93d', '#999999']
n = len(result_counts)
if n <= len(base_colors):
    colors = base_colors[:n]
else:
    colors = [base_colors[i % len(base_colors)] for i in range(n)]
explode = tuple([0.05] * n)
ax1.pie(result_counts.values, labels=result_counts.index, autopct='%1.1f%%', 
        colors=colors, explode=explode, startangle=90, textprops={'fontsize': 10, 'weight': 'bold'})
ax1.set_title(f'Trade Result Distribution\n({result_counts.sum()} Total Trades)', fontsize=12, weight='bold')

# ════════════════════════════════════════════════════════════════════
# 2. WIN/LOSS COMPARISON BAR
# ════════════════════════════════════════════════════════════════════
ax2 = plt.subplot(3, 4, 2)
settled_df = df[df['result'].isin(['WIN', 'LOSS'])]
win_loss_data = [
    len(settled_df[settled_df['result'] == 'WIN']),
    len(settled_df[settled_df['result'] == 'LOSS'])
]
bars = ax2.bar(['WINS', 'LOSSES'], win_loss_data, color=['#51cf66', '#ff6b6b'], width=0.6, edgecolor='black', linewidth=2)
for bar in bars:
    height = bar.get_height()
    ax2.text(bar.get_x() + bar.get_width()/2., height,
            f'{int(height)}\n({height/len(settled_df)*100:.1f}%)',
            ha='center', va='bottom', fontsize=10, weight='bold')
ax2.set_ylabel('Number of Trades', fontsize=11, weight='bold')
ax2.set_title('Win vs Loss Distribution\n(Settled Trades Only)', fontsize=12, weight='bold')
ax2.set_ylim(0, max(win_loss_data) * 1.15)

# ════════════════════════════════════════════════════════════════════
# 3. P&L CUMULATIVE LINE
# ════════════════════════════════════════════════════════════════════
ax3 = plt.subplot(3, 4, 3)
settled_sorted = settled_df.sort_values('timestamp').reset_index(drop=True)
cumulative_pnl = settled_sorted['pnl'].cumsum()
ax3.plot(cumulative_pnl.index, cumulative_pnl.values, marker='o', linewidth=2.5, 
         markersize=4, color='#4dabf7', label='Cumulative P&L')
ax3.axhline(y=0, color='red', linestyle='--', linewidth=2, alpha=0.7)
ax3.fill_between(cumulative_pnl.index, cumulative_pnl.values, 0, alpha=0.2, color='#4dabf7')
ax3.set_xlabel('Trade Number', fontsize=11, weight='bold')
ax3.set_ylabel('Cumulative P&L ($)', fontsize=11, weight='bold')
ax3.set_title('Cumulative P&L Over Time\n(77 Settled Trades)', fontsize=12, weight='bold')
ax3.grid(True, alpha=0.3)
ax3.legend()

# ════════════════════════════════════════════════════════════════════
# 4. AVG WIN vs AVG LOSS
# ════════════════════════════════════════════════════════════════════
ax4 = plt.subplot(3, 4, 4)
avg_win = settled_df[settled_df['result'] == 'WIN']['pnl'].mean()
avg_loss = settled_df[settled_df['result'] == 'LOSS']['pnl'].mean()
bars = ax4.bar(['Avg Win', 'Avg Loss'], [avg_win, avg_loss], 
              color=['#51cf66', '#ff6b6b'], width=0.5, edgecolor='black', linewidth=2)
for bar in bars:
    height = bar.get_height()
    ax4.text(bar.get_x() + bar.get_width()/2., height,
            f'${height:.2f}',
            ha='center', va='bottom' if height > 0 else 'top', fontsize=11, weight='bold')
ax4.axhline(y=0, color='black', linestyle='-', linewidth=1)
ax4.set_ylabel('Amount ($)', fontsize=11, weight='bold')
ax4.set_title('Average Win vs Loss\nProfit Factor: 0.82', fontsize=12, weight='bold')

# ════════════════════════════════════════════════════════════════════
# 5. BUY vs SELL PERFORMANCE
# ════════════════════════════════════════════════════════════════════
ax5 = plt.subplot(3, 4, 5)
buy_data = df[df['signal'] == 'BUY']
sell_data = df[df['signal'] == 'SELL']
buy_settled = buy_data[buy_data['result'].isin(['WIN', 'LOSS'])]
sell_settled = sell_data[sell_data['result'].isin(['WIN', 'LOSS'])]
buy_win_pct = (buy_settled['result'] == 'WIN').sum() / len(buy_settled) * 100 if len(buy_settled) > 0 else 0
sell_win_pct = (sell_settled['result'] == 'WIN').sum() / len(sell_settled) * 100 if len(sell_settled) > 0 else 0
bars = ax5.bar(['BUY', 'SELL'], [buy_win_pct, sell_win_pct], 
              color=['#4c6ef5', '#f06595'], width=0.5, edgecolor='black', linewidth=2)
for bar in bars:
    height = bar.get_height()
    ax5.text(bar.get_x() + bar.get_width()/2., height,
            f'{height:.1f}%',
            ha='center', va='bottom', fontsize=11, weight='bold')
ax5.axhline(y=50, color='orange', linestyle='--', linewidth=2, alpha=0.7, label='50% Break-even')
ax5.set_ylabel('Win Rate (%)', fontsize=11, weight='bold')
ax5.set_title('BUY vs SELL Win Rate\n(236 BUY | 23 SELL)', fontsize=12, weight='bold')
ax5.set_ylim(0, 100)
ax5.legend()

# ════════════════════════════════════════════════════════════════════
# 6. FAILED vs PENDING vs SETTLED
# ════════════════════════════════════════════════════════════════════
ax6 = plt.subplot(3, 4, 6)
execution_data = [
    len(df[df['result'] == 'FAILED']),
    len(df[df['result'] == 'PENDING']),
    len(df[df['result'].isin(['WIN', 'LOSS'])])
]
bars = ax6.bar(['Failed', 'Pending', 'Settled'], execution_data, 
              color=['#ff6b6b', '#ffd93d', '#51cf66'], width=0.6, edgecolor='black', linewidth=2)
for bar in bars:
    height = bar.get_height()
    pct = height / len(df) * 100
    ax6.text(bar.get_x() + bar.get_width()/2., height,
            f'{int(height)}\n({pct:.1f}%)',
            ha='center', va='bottom', fontsize=10, weight='bold')
ax6.set_ylabel('Number of Trades', fontsize=11, weight='bold')
ax6.set_title('Trade Execution Status\n(259 Total)', fontsize=12, weight='bold')
ax6.set_ylim(0, max(execution_data) * 1.15)

# ════════════════════════════════════════════════════════════════════
# 7. CONFIDENCE DISTRIBUTION BY RESULT
# ════════════════════════════════════════════════════════════════════
ax7 = plt.subplot(3, 4, 7)
confidence_data = [
    df[df['result'] == 'WIN']['confidence'].values,
    df[df['result'] == 'LOSS']['confidence'].values,
    df[df['result'] == 'FAILED']['confidence'].values,
    df[df['result'] == 'PENDING']['confidence'].values
]
bp = ax7.boxplot(confidence_data, labels=['WIN', 'LOSS', 'FAILED', 'PENDING'],
                 patch_artist=True, widths=0.6)
for patch, color in zip(bp['boxes'], ['#51cf66', '#ff6b6b', '#ff6b6b', '#ffd93d']):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)
ax7.set_ylabel('Confidence Level', fontsize=11, weight='bold')
ax7.set_title('Confidence Distribution by Result', fontsize=12, weight='bold')
ax7.set_ylim(0.65, 1.0)
ax7.grid(True, alpha=0.3, axis='y')

# ════════════════════════════════════════════════════════════════════
# 8. TRADES PER HOUR HEATMAP
# ════════════════════════════════════════════════════════════════════
ax8 = plt.subplot(3, 4, 8)
df['hour'] = df['timestamp'].dt.hour
df['date'] = df['timestamp'].dt.date
hourly_data = df.groupby(['date', 'hour']).size().unstack(fill_value=0)
sns.heatmap(hourly_data, annot=True, fmt='d', cmap='YlOrRd', ax=ax8, cbar_kws={'label': 'Number of Trades'})
ax8.set_title('Trades Per Hour (Heatmap)', fontsize=12, weight='bold')
ax8.set_xlabel('Hour of Day', fontsize=11, weight='bold')
ax8.set_ylabel('Date', fontsize=11, weight='bold')

# ════════════════════════════════════════════════════════════════════
# 9. P&L DISTRIBUTION HISTOGRAM
# ════════════════════════════════════════════════════════════════════
ax9 = plt.subplot(3, 4, 9)
ax9.hist(settled_df[settled_df['result'] == 'WIN']['pnl'], bins=10, alpha=0.7, 
        color='#51cf66', label='Wins', edgecolor='black', linewidth=1.5)
ax9.hist(settled_df[settled_df['result'] == 'LOSS']['pnl'], bins=10, alpha=0.7, 
        color='#ff6b6b', label='Losses', edgecolor='black', linewidth=1.5)
ax9.set_xlabel('P&L Amount ($)', fontsize=11, weight='bold')
ax9.set_ylabel('Frequency', fontsize=11, weight='bold')
ax9.set_title('P&L Distribution (Wins vs Losses)', fontsize=12, weight='bold')
ax9.legend()
ax9.grid(True, alpha=0.3, axis='y')

# ════════════════════════════════════════════════════════════════════
# 10. WIN RATE TREND (Rolling 10 trades)
# ════════════════════════════════════════════════════════════════════
ax10 = plt.subplot(3, 4, 10)
settled_sorted['win'] = (settled_sorted['result'] == 'WIN').astype(int)
settled_sorted['rolling_10_win'] = settled_sorted['win'].rolling(window=10, min_periods=1).mean() * 100
ax10.plot(settled_sorted.index, settled_sorted['rolling_10_win'], marker='o', linewidth=2.5, 
         markersize=4, color='#4c6ef5', label='Rolling 10-Trade Win%')
ax10.axhline(y=50, color='orange', linestyle='--', linewidth=2, alpha=0.7, label='50% Breakeven')
ax10.axhline(y=90, color='red', linestyle='--', linewidth=2, alpha=0.7, label='90% Target')
ax10.fill_between(settled_sorted.index, settled_sorted['rolling_10_win'], 50, alpha=0.1, color='#4c6ef5')
ax10.set_xlabel('Trade Number', fontsize=11, weight='bold')
ax10.set_ylabel('Win Rate (%)', fontsize=11, weight='bold')
ax10.set_title('Win Rate Trend (Rolling 10 Trades)', fontsize=12, weight='bold')
ax10.set_ylim(0, 110)
ax10.legend()
ax10.grid(True, alpha=0.3)

# ════════════════════════════════════════════════════════════════════
# 11. KEY METRICS TABLE
# ════════════════════════════════════════════════════════════════════
ax11 = plt.subplot(3, 4, 11)
ax11.axis('off')

total_pnl = settled_df['pnl'].sum()
win_rate = len(settled_df[settled_df['result'] == 'WIN']) / len(settled_df) * 100
profit_factor = abs(settled_df[settled_df['result'] == 'WIN']['pnl'].mean() / 
                   settled_df[settled_df['result'] == 'LOSS']['pnl'].mean())

metrics_text = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MODEL PERFORMANCE SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Total Trades:           259
Settled:                77 (29.7%)
Failed:                 161 (62.2%)
Pending:                21 (8.1%)

Win Rate:               49.4%
Total P&L:              ${total_pnl:.2f}
Avg Win:                $820.00
Avg Loss:              -$1000.00
Profit Factor:          0.82

Confidence (Avg):       88.7%
Capital at Risk:        $259,000

Circuit Breaker:        ACTIVE
Win-Rate Target:        90% (Current: 83%)
"""

ax11.text(0.05, 0.95, metrics_text, transform=ax11.transAxes, fontsize=10,
         verticalalignment='top', fontfamily='monospace',
         bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.3))

# ════════════════════════════════════════════════════════════════════
# 12. STATUS BREAKDOWN
# ════════════════════════════════════════════════════════════════════
ax12 = plt.subplot(3, 4, 12)
status_colors = {'WIN': '#51cf66', 'LOSS': '#ff6b6b', 'FAILED': '#999999', 'PENDING': '#ffd93d'}
result_types = df['result'].unique()
result_counts = [len(df[df['result'] == rt]) for rt in result_types]

wedges, texts, autotexts = ax12.pie(result_counts, labels=result_types, autopct='%1.1f%%',
                                     colors=[status_colors.get(rt, '#cccccc') for rt in result_types],
                                     startangle=45, textprops={'fontsize': 10, 'weight': 'bold'})
for autotext in autotexts:
    autotext.set_color('white')
    autotext.set_weight('bold')
ax12.set_title('Execution Status Distribution', fontsize=12, weight='bold')

plt.tight_layout()
plt.savefig('/home/akill-sud/Desktop/tg_algo_enhanced./tg_algo/trade_analysis_graphs.png', dpi=300, bbox_inches='tight')
print("✓ Graph saved: trade_analysis_graphs.png")
print("\n" + "="*70)
print("TRADE PERFORMANCE ANALYSIS - VISUAL REPORT GENERATED")
print("="*70)

# Print detailed summary
print("\n📊 KEY FINDINGS:\n")
print(f"  • Win Rate: 49.4% (Below 50% breakeven - LOSING SYSTEM)")
print(f"  • Total P&L: ${total_pnl:.2f} (NEGATIVE)")
print(f"  • Profit Factor: 0.82 (Below 1.0 - UNPROFITABLE)")
print(f"  • Failed Trades: 161 (62.2% execution failure rate - HIGH RISK)")
print(f"  • Circuit Breaker: TRIGGERED - System paused at 83% win-rate")
print(f"  • Confidence: 88.7% avg (HIGH CONFIDENCE BUT INACCURATE SIGNALS)")
print(f"\n⚠️  CRITICAL ISSUES:")
print(f"  1. Signal accuracy is poor (49.4% win rate vs 90% target)")
print(f"  2. High execution failure rate (62.2% failed trades)")
print(f"  3. BUY signals performing worse (47.6%) than SELL signals (57.1%)")
print(f"  4. System generating false high-confidence signals")
print(f"\n📈 POSITIVE ASPECTS:")
print(f"  • SELL signals show promise (57.1% win rate)")
print(f"  • Consistent trade sizing ($1000 per trade)")
print(f"  • Circuit breaker protection is working")
print("\n" + "="*70)

plt.show()
