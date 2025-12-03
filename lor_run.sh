#!/bin/bash

rm -rf data

PREV_JOB=""

for BENCH in a2a_cong a2a;
do
    CONFIG_FILE="huawei/h_${BENCH}.json"
    echo $CONFIG_FILE

    # Update JSON with previous job ID
    if [ -n "$PREV_JOB" ]; then
        # Replace prev_job: "<something>"
        jq --arg jid "$PREV_JOB" '.global_options.prevjob = $jid' \
           "$CONFIG_FILE" > tmp.json && mv tmp.json "$CONFIG_FILE"
    else
        # First iteration: set empty value
        jq '.global_options.prevjob = "-1"' "$CONFIG_FILE" > tmp.json && mv tmp.json "$CONFIG_FILE"
    fi

    JOBID=$(python cli.py --preset leonardo --config "$CONFIG_FILE" | awk '/Submitted batch job/{print $4}')
    PREV_JOB=$JOBID
    echo "Extracted job ID: $PREV_JOB" 
done

# CONFIG_FILE="huawei/h_all2all.json"
# python cli.py --preset leonardo --config "$CONFIG_FILE" | awk '/Submitted batch job/{print $4}'
