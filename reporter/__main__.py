"""Entry point and argparse dispatch."""
import sys


def cli() -> int:
    """Return a process exit code. Implemented in Task 8."""
    print("ai-usage-reporter: not yet implemented", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(cli())
