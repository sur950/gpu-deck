#!/bin/bash

# Test runner for the GPU Deck backend. Always runs from apps/fastapi.
#   ./tests/scripts/run_tests.sh [all|unit|integration|e2e|coverage|coverage-html|help] [extra pytest args]

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

if [ -z "$VIRTUAL_ENV" ] && [ -d "venv/bin" ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

cmd="${1:-all}"
shift || true

case "$cmd" in
    all)
        print_info "Running all tests"
        pytest tests "$@"
        ;;
    unit)
        print_info "Running unit tests (no external services)"
        pytest tests/unit -m "not integration and not e2e" "$@"
        ;;
    integration)
        print_info "Running integration tests (real local Postgres + Redis)"
        pytest tests/integration -m integration "$@"
        ;;
    e2e)
        print_info "Running end-to-end tests"
        pytest tests/e2e -m e2e "$@"
        ;;
    coverage)
        pytest tests --cov=app --cov-report=term-missing "$@"
        ;;
    coverage-html)
        pytest tests --cov=app --cov-report=html "$@"
        print_success "HTML report: htmlcov/index.html"
        ;;
    help|--help|-h)
        echo "Usage: $0 [all|unit|integration|e2e|coverage|coverage-html] [pytest args]"
        ;;
    *)
        print_error "Unknown command: $cmd"
        exit 1
        ;;
esac
