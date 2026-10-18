import argparse
import os
from agents.agent_rl_learning import RLFeedbackAgent

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
JOURNAL = os.path.join(os.path.dirname(__file__), 'logs', 'trade_journal.csv')


def parse_args():
    parser = argparse.ArgumentParser(description='Train RL q-table from trade journal')
    parser.add_argument('--journal', default=JOURNAL, help='Path to trade journal CSV')
    parser.add_argument('--model-dir', default=MODEL_DIR, help='Directory to save RL q-table')
    parser.add_argument('--epochs', type=int, default=3, help='Number of replay passes over the journal')
    parser.add_argument('--shuffle', action='store_true', help='Shuffle journal rows between epochs')
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    agent = RLFeedbackAgent(args.model_dir)
    print(f'Training RL agent from journal: {args.journal}')
    agent.train_from_logs(args.journal, epochs=args.epochs, shuffle=args.shuffle)
    print('Q-table saved to', agent.qtable_path)
