#!/usr/bin/env bash
# Export TIMES → PyPSA coupling inputs and optionally run pypsa-wal Snakemake.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: run_coupled.sh <coupling_dir> [--snakemake] [snakemake args...]

Export a soft-linking bundle under <coupling_dir>, then optionally invoke
pypsa-wal Snakemake with coupling_dir passed via --config.

By default only export-coupling runs. Pass --snakemake to build the four
wallon_demands CSVs in pypsa-wal (remaining args are forwarded to snakemake).

Requires <coupling_dir>/times/scenario.vd or exactly one <coupling_dir>/times/*.vd
before export (unless --vd is supplied via times-pypsa export-coupling manually).
EOF
}

if [[ $# -lt 1 ]]; then
    usage
    exit 1
fi

COUPLING_DIR="$(realpath "$1")"
shift

RUN_SNAKEMAKE=false
SNAKEMAKE_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --snakemake)
            RUN_SNAKEMAKE=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            SNAKEMAKE_ARGS+=("$1")
            shift
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMES_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYPSA_WAL="$(cd "${TIMES_ROOT}/../pypsa-wal" && pwd)"

TIMES_VD=""
if [[ -f "${COUPLING_DIR}/times/scenario.vd" ]]; then
    TIMES_VD="${COUPLING_DIR}/times/scenario.vd"
else
    mapfile -t VD_FILES < <(find "${COUPLING_DIR}/times" -maxdepth 1 -name '*.vd' 2>/dev/null | sort)
    if [[ ${#VD_FILES[@]} -eq 1 ]]; then
        TIMES_VD="${VD_FILES[0]}"
    elif [[ ${#VD_FILES[@]} -gt 1 ]]; then
        echo "error: multiple .vd files in ${COUPLING_DIR}/times/; expected scenario.vd or a single file" >&2
        exit 1
    else
        echo "error: no .vd file found under ${COUPLING_DIR}/times/" >&2
        exit 1
    fi
fi

HORIZONS="2025,2030,2040,2050"
echo "Exporting coupling bundle to ${COUPLING_DIR} from ${TIMES_VD}"
times-pypsa export-coupling \
    --coupling-dir "${COUPLING_DIR}" \
    --vd "${TIMES_VD}" \
    --horizons "${HORIZONS}"

if [[ "${RUN_SNAKEMAKE}" != true ]]; then
    echo "Export complete. Pass --snakemake to run pypsa-wal build_wallon_demands."
    exit 0
fi

RUN_NAME="${PYPSA_RUN_NAME:-walloon-model}"
DEFAULT_TARGETS=()
for h in 2025 2030 2040 2050; do
    DEFAULT_TARGETS+=("resources/${RUN_NAME}/wallon_demands_${h}.csv")
done

if [[ ${#SNAKEMAKE_ARGS[@]} -eq 0 ]]; then
    SNAKEMAKE_ARGS=("${DEFAULT_TARGETS[@]}")
fi

echo "Running snakemake in ${PYPSA_WAL} with coupling_dir=${COUPLING_DIR}"
cd "${PYPSA_WAL}"
snakemake \
    --configfile config/config.walloon.yaml \
    --config "coupling_dir=${COUPLING_DIR}" \
    "${SNAKEMAKE_ARGS[@]}"
