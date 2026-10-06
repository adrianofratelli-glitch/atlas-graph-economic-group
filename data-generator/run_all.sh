#!/bin/bash
# Atalho histórico. O pipeline completo (dados, índices B-tree, estado de
# revisão, vetores, Atlas Search/Vector Search e conferência) vive em
# scripts/reset_demo.py, que é o comando único e idempotente da demo.
#
# Volume: PEOPLE, COMPANIES, ECON_GROUPS, SHOWCASE. Não use `GROUPS`: no bash é
# variável especial (lista de gids do usuário) e atribuir a ela não tem efeito —
# era por isso que este script gerava sempre 20 grupos, o gid de `staff` no
# macOS, em vez de 40.000.
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
exec "$BASE/.venv/bin/python" "$BASE/scripts/reset_demo.py" "$@"
