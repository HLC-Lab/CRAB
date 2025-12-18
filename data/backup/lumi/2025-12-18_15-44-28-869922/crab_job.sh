#!/bin/bash

#SBATCH --job-name=crab_All-to-All
#SBATCH --output=./data/lumi/2025-12-18_15-44-28-869922/slurm_output.log
#SBATCH --error=./data/lumi/2025-12-18_15-44-28-869922/slurm_error.log
#SBATCH --nodes=8
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --dependency=afterany:15427174
#SBATCH --partition=standard-g
#SBATCH --account=project_465001736
#SBATCH --time=01:00:00

source /pfs/lustrep4/users/pasqualo/CRAB/.venv/bin/activate

/pfs/lustrep4/users/pasqualo/CRAB/.venv/bin/python /pfs/lustrep4/users/pasqualo/CRAB/cli.py --worker --workdir ./data/lumi/2025-12-18_15-44-28-869922
