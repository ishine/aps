#!/usr/bin/env bash

# Copyright 2019 Jian Wu
# License: Apache 2.0 (http://www.apache.org/licenses/LICENSE-2.0)

set -eu

gpu=0
dict=""
space=""
nbest=1
channel=-1
max_len=500
min_len=1
max_len_ratio=1
min_len_ratio=0
beam_size=16
batch_size=""
function="beam_search"
len_norm=true
len_penalty=0
cov_penalty=0
cov_threshold=0
eos_threshold=0
temperature=1
am_tag="best"
lm_tag="best"
lm=""
lm_weight=0
ctc_weight=0
spm=""
dump_align=""
segment=""
text=""
score=false
dec_prefix=""

echo "$0 $*"

. ./utils/parse_options.sh || exit 1

[ $# -ne 3 ] && echo "Script format error: $0 <exp-dir> <tst-scp> <dec-dir>" && exit 1

exp_dir=$1
tst_scp=$2
dec_dir=$3

[ ! -f $tst_scp ] && echo "$0: missing test wave script: $tst_scp" && exit 0
[ ! -d $exp_dir ] && echo "$0: missing experiment directory: $exp_dir" && exit 0

mkdir -p $dec_dir $dec_dir/log

if [ -z $dec_prefix ]; then
  # generate random string
  random_str=$(date +%s%N | md5sum | cut -c 1-9)
  dec_prefix=beam${beam_size}_${random_str}
fi

if [ -z $batch_size ]; then
  cmd/decode.py \
    $tst_scp \
    $dec_dir/${dec_prefix}.decode \
    --segment "$segment" \
    --beam-size $beam_size \
    --device-id $gpu \
    --channel $channel \
    --dict "$dict" \
    --am $exp_dir \
    --lm "$lm" \
    --am-tag $am_tag \
    --lm-tag $lm_tag \
    --spm "$spm" \
    --temperature $temperature \
    --lm-weight $lm_weight \
    --ctc-weight $ctc_weight \
    --space "$space" \
    --nbest $nbest \
    --dump-nbest $dec_dir/${dec_prefix}.nbest \
    --dump-align "$dump_align" \
    --max-len $max_len \
    --min-len $min_len \
    --max-len-ratio $max_len_ratio \
    --min-len-ratio $min_len_ratio \
    --len-norm $len_norm \
    --function $function \
    --len-penalty $len_penalty \
    --cov-penalty $cov_penalty \
    --cov-threshold $cov_threshold \
    --eos-threshold $eos_threshold \
    > $dec_dir/log/decode.$dec_prefix.log 2>&1
else
  cmd/decode_batch.py \
    $tst_scp \
    $dec_dir/${dec_prefix}.decode \
    --segment "$segment" \
    --beam-size $beam_size \
    --batch-size $batch_size \
    --device-id $gpu \
    --channel $channel \
    --dict "$dict" \
    --am $exp_dir \
    --lm "$lm" \
    --am-tag $am_tag \
    --lm-tag $lm_tag \
    --spm "$spm" \
    --space "$space" \
    --temperature $temperature \
    --lm-weight $lm_weight \
    --ctc-weight $ctc_weight \
    --nbest $nbest \
    --dump-nbest $dec_dir/${dec_prefix}.nbest \
    --dump-align "$dump_align" \
    --max-len $max_len \
    --min-len $min_len \
    --max-len-ratio $max_len_ratio \
    --min-len-ratio $min_len_ratio \
    --len-norm $len_norm \
    --len-penalty $len_penalty \
    --cov-penalty $cov_penalty \
    --cov-threshold $cov_threshold \
    --eos-threshold $eos_threshold \
    > $dec_dir/log/decode.$dec_prefix.log 2>&1
fi

if $score ; then
  [ -z $text ] && echo "for --score true, you must given --text <reference-transcription>" && exit -1
  ./cmd/compute_wer.py $dec_dir/${dec_prefix}.decode $text \
    | tee $dec_dir/${dec_prefix}.wer
fi

echo "$0 $*: Done"
