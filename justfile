# Python Monorepo Justfile
# Usage: just <recipe>

# Default recipe - show available commands
default:
    @just --list

# Prune an app for Docker build (turbo prune --docker equivalent)
# Output: out/<app>/json/ and out/<app>/full/
# Usage: just prune <app-name>
prune app:
    @echo "Pruning {{app}} for Docker build..."
    python scripts/prune.py {{app}}

# Prune with custom output directory
prune-to app out:
    @echo "Pruning {{app}} to {{out}}..."
    python scripts/prune.py {{app}} --out-dir {{out}}

# Prune order-service
prune-order-service:
    @just prune order-service

# Prune user-dashboard
prune-user-dashboard:
    @just prune user-dashboard

# Prune report-generator
prune-report-generator:
    @just prune report-generator

# Prune all apps
prune-all:
    @just prune order-service
    @just prune user-dashboard
    @just prune report-generator

# Clean pruned outputs
clean-prune:
    rm -rf out/

# Run lint on all packages
lint:
    uv run ruff check .

# Run format on all packages
format:
    uv run ruff format .

# Run tests
test:
    uv run pytest

# Sync all dependencies
sync:
    uv sync --all-packages

# Lock dependencies
lock:
    uv lock

# Show dependency tree for an app
deps app:
    @echo "Dependencies for {{app}}:"
    @python -c "
import sys
sys.path.insert(0, 'scripts')
from prune import discover_packages, collect_all_dependencies
from pathlib import Path
root = Path('.')
packages = discover_packages(root)
deps = collect_all_dependencies('{{app}}', packages)
deps.discard('{{app}}')
for d in sorted(deps):
    print(f'  - {d}')
"

# Show prune output structure
show-prune app:
    @echo "Prune output structure for {{app}}:"
    @if [ -d "out/{{app}}" ]; then \
        find out/{{app}} -type f | head -30; \
    else \
        echo "Not pruned yet. Run: just prune {{app}}"; \
    fi
