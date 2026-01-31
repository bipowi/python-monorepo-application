# Python Monorepo Justfile
# Usage: just <recipe>

# Default recipe - show available commands
default:
    @just --list

# Prune an app for Docker build (turbo prune --docker equivalent)
# Usage: just prune <app-name> [out-dir]
prune app out="out":
    @echo "Pruning {{app}} for Docker build..."
    python scripts/prune.py {{app}} --out-dir {{out}}

# Prune order-service
prune-order-service out="out/order-service":
    @just prune order-service {{out}}

# Prune user-dashboard
prune-user-dashboard out="out/user-dashboard":
    @just prune user-dashboard {{out}}

# Prune report-generator
prune-report-generator out="out/report-generator":
    @just prune report-generator {{out}}

# Prune all apps
prune-all:
    @just prune order-service out/order-service
    @just prune user-dashboard out/user-dashboard
    @just prune report-generator out/report-generator

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

# Build a specific app (after prune)
# Usage: just docker-build <app-name>
docker-build app:
    @echo "Building Docker image for {{app}}..."
    @if [ ! -d "out/{{app}}" ]; then \
        echo "Pruned output not found. Running prune first..."; \
        just prune {{app}} out/{{app}}; \
    fi
    @echo "Docker build context ready at: out/{{app}}"
    @echo "Run: docker build -t {{app}} -f Dockerfile out/{{app}}"

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
