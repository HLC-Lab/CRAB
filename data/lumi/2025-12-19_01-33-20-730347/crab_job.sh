#!/bin/bash

#SBATCH --job-name=crab_All-to-All
#SBATCH --output=./data/lumi/2025-12-19_01-33-20-730347/slurm_output.log
#SBATCH --error=./data/lumi/2025-12-19_01-33-20-730347/slurm_error.log
#SBATCH --nodes=256
#SBATCH --ntasks-per-node=32
#SBATCH --exclusive
#SBATCH --dependency=afterany:15437042
#SBATCH --partition=standard-g
#SBATCH --account=project_465001736
#SBATCH --time=01:00:00

source /pfs/lustrep4/users/pasqualo/CRAB/.venv/bin/activate

/pfs/lustrep4/users/pasqualo/CRAB/.venv/bin/python /pfs/lustrep4/users/pasqualo/CRAB/cli.py --worker --workdir ./data/lumi/2025-12-19_01-33-20-730347
