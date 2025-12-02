#!/bin/bash

rm -rf data

# PREV_JOB=""

# for BENCH in 8B 64B 512B 4KiB 32KiB 256KiB 2MiB 16MiB 128MiB;
# do
#     CONFIG_FILE="huawei/h_${BENCH}_a2a.json"
#     echo $CONFIG_FILE

#     # Update JSON with previous job ID
#     if [ -n "$PREV_JOB" ]; then
#         # Replace prev_job: "<something>"
#         jq --arg jid "$PREV_JOB" '.global_options.prev_job = $jid' \
#            "$CONFIG_FILE" > tmp.json && mv tmp.json "$CONFIG_FILE"
#     else
#         # First iteration: set empty value
#         jq '.global_options.prev_job = "-1"' "$CONFIG_FILE" > tmp.json && mv tmp.json "$CONFIG_FILE"
#     fi

#     JOBID=$(python cli.py --preset leonardo --config "$CONFIG_FILE" | awk '/Submitted batch job/{print $4}')
#     PREV_JOB=$JOBID
#     echo "Extracted job ID: $PREV_JOB" 
# done

CONFIG_FILE="h_all2all.json"
JOBID=$(python cli.py --preset leonardo --config "$CONFIG_FILE" | awk '/Submitted batch job/{print $4}')
PREV_JOB=$JOBID
echo "Extracted job ID: $PREV_JOB" 