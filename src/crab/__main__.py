import sys


def main():
    from crab.cli.main import cli_router

    sys.exit(cli_router())


if __name__ == "__main__":
    main()
