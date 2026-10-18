#!/usr/bin/env bash
set -u
source /home/akill-sud/anaconda3/etc/profile.d/conda.sh
conda activate tg_algo
cd '/home/akill-sud/Documents/projects/algo_main_tg_updated (1)/algo_main_tg'

restart_count=0
while true; do
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] restarting bot" >> logs/run_forever.log
  python multi_agent_orchestrator.py --max-daily-trades 120 --max-daily-loss 20000
  code=$?
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] exit_code=${code}" >> logs/run_forever.log
  if [ "$code" != "75" ]; then
    echo "non-recovery exit, stopping" >> logs/run_forever.log
    exit "$code"
  fi
  restart_count=$((restart_count + 1))
  if [ "$restart_count" -ge 6 ]; then
    echo "restart limit reached" >> logs/run_forever.log
    exit 75
  fi
  echo "restarting in 10s" >> logs/run_forever.log
  sleep 10
 done
