#!/bin/bash

#SBATCH --job-name=crab_All-Gather
#SBATCH --output=./data/leonardo/2025-12-19_16-48-13-219815/slurm_output.log
#SBATCH --error=./data/leonardo/2025-12-19_16-48-13-219815/slurm_error.log
#SBATCH --nodes=128
#SBATCH --ntasks-per-node=32
#SBATCH --exclusive
#SBATCH --dependency=afterany:29457335
#SBATCH --partition=boost_usr_prod
#SBATCH --account=IscrB_SWING
#SBATCH --gres=gpu:4
#SBATCH --qos=boost_qos_bprod
#SBATCH --time=01:00:00

source /leonardo/home/userexternal/lpiarull/CRAB/.venv/bin/activate

/leonardo/home/userexternal/lpiarull/CRAB/.venv/bin/python /leonardo/home/userexternal/lpiarull/CRAB/cli.py --worker --workdir ./data/leonardo/2025-12-19_16-48-13-219815
