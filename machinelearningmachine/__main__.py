"""
Entry point so the package can be launched directly:

    python -m machinelearningmachine            # launch the web dashboard
    python -m machinelearningmachine run ...    # run a dialogue from the CLI
"""

from .cli import main

if __name__ == "__main__":
    main()
