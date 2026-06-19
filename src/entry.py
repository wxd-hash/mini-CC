"""Entry point for the 'minicc' console script."""
import sys
from pathlib import Path

# Ensure project root is on sys.path for 'main:main' to work from any directory
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Import and dispatch — defer so the path fix takes effect
from main import main  # noqa: E402

main()
