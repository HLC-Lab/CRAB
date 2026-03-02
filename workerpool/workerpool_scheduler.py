# N process apps always active. When one dies, another spawns.
import argparse as ap




def run_scheduler(app_path, nodelist, pool_size):
    # Ex
    return None



if __name__ == '__main__':

    parser = ap.ArgumentParser(description="Workerpool Scheduler for CRAB.")

    parser.add_argument("--app-path", help="Path of the app to execute")
    parser.add_argument("--pool-size", help="Maximum size of the pool")
    parser.add_argument("--nodelist", help="List of the nodes")
    # Maybe an array of arguments is needed by the proxies

    # 3. Parse arguments
    args = parser.parse_args()

    run_scheduler(args.nodelist)