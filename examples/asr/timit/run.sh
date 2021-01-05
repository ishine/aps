#!/usr/bin/env bash

# Copyright 2020 Jian Wu
# License: Apache 2.0 (http://www.apache.org/licenses/LICENSE-2.0)

set -eu

timit_data=/scratch/jwu/TIMIT-LDC93S1/TIMIT
dataset="timit"
stage="1-3"
# training
gpu=0
exp=1a # load training configuration in conf/timit/1a.yaml
seed=777
epochs=100
tensorboard=false
batch_size=12
num_workers=2
prog_interval=100

# decoding
beam_size=8
nbest=8

. ./utils/parse_options.sh || exit 1

beg=$(echo $stage | awk -F '-' '{print $1}')
end=$(echo $stage | awk -F '-' '{print $2}')
[ -z $end ] && end=$beg

if [ $end -ge 1 ] && [ $beg -le 1 ]; then
  echo "Stage 1: preparing data ..."
  ./local/timit_data_prep.sh --dataset $dataset $timit_data
fi

if [ $end -ge 2 ] && [ $beg -le 2 ]; then
  echo "Stage 1: training AM ..."
  ./scripts/train.sh \
    --seed $seed \
    --gpu $gpu \
    --epochs $epochs \
    --num-workers $num_workers \
    --batch-size $batch_size \
    --tensorboard $tensorboard \
    --prog-interval $prog_interval \
    am $dataset $exp
fi

if [ $end -ge 3 ] && [ $beg -le 3 ]; then
  echo "Stage 3: decoding ..."
  # decoding
  ./scripts/decode.sh \
    --gpu $gpu \
    --beam-size $beam_size \
    --nbest $nbest \
    --max-len 75 \
    --dict data/timit/dict \
    --function "beam_search" \
    $dataset $exp \
    data/timit/test/wav.scp \
    exp/timit/$exp/dec
  # wer
  ./cmd/compute_wer.py exp/timit/$exp/dec/beam${beam_size}.decode \
    data/timit/test/text
fi
