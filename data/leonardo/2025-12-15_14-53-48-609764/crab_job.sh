#!/bin/bash

#SBATCH --job-name=crab_All-Gather
#SBATCH --output=./data/leonardo/2025-12-15_14-53-48-609764/slurm_output.log
#SBATCH --error=./data/leonardo/2025-12-15_14-53-48-609764/slurm_error.log
#SBATCH --nodes=64
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --dependency=afterany:29062378
#SBATCH --partition=boost_usr_prod
#SBATCH --account=IscrB_SWING
#SBATCH --time=01:00:00

source /leonardo/home/userexternal/lpiarull/CRAB/.venv/bin/activate

/leonardo/home/userexternal/lpiarull/CRAB/.venv/bin/python /leonardo/home/userexternal/lpiarull/CRAB/cli.py --worker --workdir ./data/leonardo/2025-12-15_14-53-48-609764
