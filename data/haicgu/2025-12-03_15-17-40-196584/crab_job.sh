#!/bin/bash

#SBATCH --job-name=crab_All-to-All
#SBATCH --output=./data/haicgu/2025-12-03_15-17-40-196584/slurm_output.log
#SBATCH --error=./data/haicgu/2025-12-03_15-17-40-196584/slurm_error.log
#SBATCH --nodes=8
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --partition=cn-eth
#SBATCH --account=dcn
#SBATCH --gres=tmpfs:0
#SBATCH --time=01:00:00

source /home/lpiarulli/CRAB/.venv/bin/activate

/home/lpiarulli/CRAB/.venv/bin/python /home/lpiarulli/CRAB/cli.py --worker --workdir ./data/haicgu/2025-12-03_15-17-40-196584
