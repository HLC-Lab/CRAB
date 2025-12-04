#!/bin/bash

#SBATCH --job-name=crab_All-to-All
#SBATCH --output=./data/haicgu/2025-12-04_11-54-00-069076/slurm_output.log
#SBATCH --error=./data/haicgu/2025-12-04_11-54-00-069076/slurm_error.log
#SBATCH --nodes=8
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --dependency=afterany:28990
#SBATCH --partition=cn-eth
#SBATCH --account=dcn
#SBATCH --gres=tmpfs:0
#SBATCH --time=01:00:00

source /home/lpiarulli/CRAB/.venv/bin/activate

/home/lpiarulli/CRAB/.venv/bin/python /home/lpiarulli/CRAB/cli.py --worker --workdir ./data/haicgu/2025-12-04_11-54-00-069076
