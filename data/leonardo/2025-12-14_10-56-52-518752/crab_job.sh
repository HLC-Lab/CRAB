#!/bin/bash

#SBATCH --job-name=crab_All-to-All
#SBATCH --output=./data/leonardo/2025-12-14_10-56-52-518752/slurm_output.log
#SBATCH --error=./data/leonardo/2025-12-14_10-56-52-518752/slurm_error.log
#SBATCH --nodes=10
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --partition=boost_usr_prod
#SBATCH --account=IscrB_SWING
#SBATCH --mem=500G
#SBATCH --auks=yes
#SBATCH --time=01:00:00

source /leonardo/home/userexternal/lpiarull/CRAB/.venv/bin/activate

/leonardo/home/userexternal/lpiarull/CRAB/.venv/bin/python /leonardo/home/userexternal/lpiarull/CRAB/cli.py --worker --workdir ./data/leonardo/2025-12-14_10-56-52-518752
