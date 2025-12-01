#!/bin/bash

#SBATCH --job-name=crab_64B
#SBATCH --output=./data/leonardo/2025-12-01_12-36-45-613340/slurm_output.log
#SBATCH --error=./data/leonardo/2025-12-01_12-36-45-613340/slurm_error.log
#SBATCH --nodes=8
#SBATCH --ntasks-per-node=1
#SBATCH --dependency=afterany:27323679
#SBATCH --exclusive
#SBATCH --partition=boost_usr_prod
#SBATCH --account=IscrB_SWING
#SBATCH --gres=tmpfs:0
#SBATCH --time=01:00:00

source /leonardo/home/userexternal/lpiarull/CRAB/.venv/bin/activate

/leonardo/home/userexternal/lpiarull/CRAB/.venv/bin/python /leonardo/home/userexternal/lpiarull/CRAB/cli.py --worker --workdir ./data/leonardo/2025-12-01_12-36-45-613340
