#!/usr/bin/env bash

# Copyright 2017 Xingyu Na
# Apache 2.0

set -eu

if [ $# != 2 ]; then
  echo "Usage: $0 <corpus-path> <data-path>"
  echo " $0 /export/a05/xna/data/data_aidatatang_200zh data/aidatatang"
  exit 1;
fi

aidatatang_audio_dir=$1/corpus
aidatatang_text=$1/transcript/aidatatang_200_zh_transcript.txt
data=$2

train_dir=$data/local/train
dev_dir=$data/local/dev
test_dir=$data/local/test
tmp_dir=$data/local/tmp

mkdir -p $train_dir
mkdir -p $dev_dir
mkdir -p $test_dir
mkdir -p $tmp_dir

# data directory check
if [ ! -d $aidatatang_audio_dir ] || [ ! -f $aidatatang_text ]; then
  echo "Error: $0 requires two directory arguments"
  exit 1;
fi

echo "**** Creating aidatatang data folder ****"

# find wav audio file for train, dev and test resp.
find $aidatatang_audio_dir -iname "*.wav" > $tmp_dir/wav.flist
n=`cat $tmp_dir/wav.flist | wc -l`
[ $n -ne 237265 ] && "echo Warning: expected 237265 data files, found $n"

grep -i "corpus/train" $tmp_dir/wav.flist > $train_dir/wav.flist || exit 1;
grep -i "corpus/dev" $tmp_dir/wav.flist > $dev_dir/wav.flist || exit 1;
grep -i "corpus/test" $tmp_dir/wav.flist > $test_dir/wav.flist || exit 1;

rm -r $tmp_dir

# Transcriptions preparation
for dir in $train_dir $dev_dir $test_dir; do
  echo Preparing $dir transcriptions
  sed -e 's/\.wav//' $dir/wav.flist | awk -F '/' '{print $NF}' > $dir/utt.list
  paste -d' ' $dir/utt.list $dir/wav.flist > $dir/wav.scp_all
  utils/filter_scp.pl -f 1 $dir/utt.list $aidatatang_text | sed 's/Ａ/A/g' > $dir/transcripts.txt
  awk '{print $1}' $dir/transcripts.txt > $dir/utt.list
  utils/filter_scp.pl -f 1 $dir/utt.list $dir/wav.scp_all | sort -u > $dir/wav.scp
  sort -u $dir/transcripts.txt > $dir/text
done

mkdir -p $data/train $data/dev $data/test

for f in wav.scp text; do
  cp $train_dir/$f $data/train/$f || exit 1;
  cp $dev_dir/$f $data/dev/$f || exit 1;
  cp $test_dir/$f $data/test/$f || exit 1;
done

echo "$0: aidatatang_200zh data preparation succeeded"
exit 0;
