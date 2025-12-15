#!/bin/bash

#SBATCH --job-name=crab_All-Gather
#SBATCH --output=./data/leonardo/2025-12-14_20-11-41-472951/slurm_output.log
#SBATCH --error=./data/leonardo/2025-12-14_20-11-41-472951/slurm_error.log
#SBATCH --nodes=10
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --dependency=afterany:29030470
#SBATCH --partition=boost_usr_prod
#SBATCH --account=IscrB_SWING
#SBATCH --time=01:00:00

source /leonardo/home/userexternal/lpiarull/CRAB/.venv/bin/activate

/leonardo/home/userexternal/lpiarull/CRAB/.venv/bin/python /leonardo/home/userexternal/lpiarull/CRAB/cli.py --worker --workdir ./data/leonardo/2025-12-14_20-11-41-472951
