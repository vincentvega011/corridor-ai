#!/usr/bin/env bash
# LoRA fine-tune of the parser (Qwen3 1.7B 4-bit, MLX). Reproduce with: make finetune
# Low-memory settings for a fanless 16 GB MacBook Air: batch 2, 12 LoRA layers, gradient checkpointing.
# max-seq-length 1280: the longest training example is about 1,100 tokens, so nothing is truncated.
# Measured on the M4 Air: about 10 s per iteration (batch 2, 1,000 token prompts), so 300 iterations is about 50 min.
# The loss is on the completion only (--mask-prompt). The data already ends in an empty think block, the same
# prompt shape the server uses with enable_thinking false.
set -euo pipefail
cd "$(dirname "$0")/../../backend"
STAMP="${RUN_STAMP:-$(date +%Y%m%dT%H%M%S)}"
RUN="../models/finetune/runs/$STAMP"
mkdir -p "$RUN"
ITERS="${ITERS:-300}"
cat > "$RUN/config.json" <<JSON
{"model": "mlx-community/Qwen3-1.7B-4bit", "data": "models/finetune/data", "iters": $ITERS, "batch_size": 2,
 "num_layers": 12, "learning_rate": 1e-4, "max_seq_length": 1280, "grad_checkpoint": true, "mask_prompt": true,
 "seed": 0, "steps_per_eval": 100, "val_batches": 8, "save_every": 100, "fine_tune_type": "lora"}
JSON
uv run python -m mlx_lm lora --model mlx-community/Qwen3-1.7B-4bit --train --data ../models/finetune/data \
  --fine-tune-type lora --iters "$ITERS" --batch-size 2 --num-layers 12 --learning-rate 1e-4 \
  --max-seq-length 1280 --grad-checkpoint --mask-prompt --seed 0 --steps-per-eval 100 --val-batches 8 \
  --save-every 100 --adapter-path "$RUN"
echo "run: $RUN"
