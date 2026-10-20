#!/usr/bin/env bash
# After training exits: ship-gate eval of the current and the fine-tuned parser, ONE server at a time (memory rule).
# Output: logs/shipgate_*.json and logs/after_train.log. The adopt-or-keep decision is made by a person or a session.
set -uo pipefail
cd "$(dirname "$0")/../../backend"
while pgrep -f "mlx_lm lora" > /dev/null; do sleep 20; done
RUN=$(ls -dt ../models/finetune/runs/2026* | head -1)
echo "training finished, run $RUN, $(date +%H:%M)"
serve() {  # $1 = extra args; waits until the server answers
  nohup uv run --group local mlx_lm.server --model mlx-community/Qwen3-1.7B-4bit --port 8081 $1 > ../logs/parser_gate.log 2>&1 &
  for i in $(seq 1 90); do curl -s localhost:8081/v1/models > /dev/null 2>&1 && return 0; sleep 2; done; return 1
}
stop() { pkill -f "mlx_lm.server" ; sleep 5; }
stop
if serve ""; then
  uv run python ../eval/nlu/ship_gate.py --label current
  uv run python ../eval/nlu/ship_gate.py --label current --zero-shot
fi
stop
if serve "--adapter-path $RUN"; then
  uv run python ../eval/nlu/ship_gate.py --label finetuned
  uv run python ../eval/nlu/ship_gate.py --label finetuned --zero-shot
fi
stop
echo "ship gate done $(date +%H:%M)"
