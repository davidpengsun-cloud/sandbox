#!/bin/sh
# The image's one entry point (Dockerfile): the app and the jobs, each command today's kge commands with nothing of its
# own, for the profile PROFILE names (else the registry's default, deploy/profiles.yaml). Settings after the command are
# kge's, NAME=value as on its command line (PRINCIPAL=<steward>, HASH=<hash>, REPEATS=1), and set in the
# environment too; the environment's are kge's as ever (docker run -e PROFILE=<id>). The image has no make: kge, the
# console script the package installs, takes the Makefile's targets' names and settings.
#
#   serve      kge serve: the API and the page on 0.0.0.0:$PORT (default 8000), the address it names itself by
#              (kge serve's API, its run state's api.json) API_URL's where set
#   seed       kge estate: the client's estate built, certified and landed in build/landing/<client>/
#   onboard    with page-written units, kge onboard rebuilds, certifies and locks them before compile/load/check;
#              without any units, kge check and compile, then kge load and kge margin on the candidate, unless the
#              pack compiles to current and current is loaded on this graph store (kge/pipeline/profile.py --make,
#              loaded=yes): then nothing is loaded or checked, as kge onboard skips it, so the served hash is never
#              reloaded or unmarked (with WAREHOUSE_BACKEND or RETRIEVAL_BACKEND bigquery, kge load syncs the
#              candidate to BigQuery before its margin is checked: the Makefile's BIGQUERY_SYNC)
#   onboard-request  validate and run the page's store-backed request (the Cloud Run job's default command)
#   sign-off   kge sign-off PRINCIPAL=<steward>
#   release    kge release; the next serve serves it
#   eval       kge ab, the A/B harness
#   baseline   the profile's own A/B baseline, recorded on this setup: kge ab, then kge baseline on that run into the
#              pack's baselines/<graph store>-<UTC time>.json (with a store, put in its clients/<client>/baselines/); it
#              prints the setup-client line that points the profile at it
#   passwords  kge passwords: a password per person, in the run state (with a store, put in it: below)
#
# The object store (kge/adapters/objstore/, its commands kge/pipeline/objstore.py). With STORE_URL unset, each command is today's kge commands and nothing else.
# With it set, the store holds what is a client's, never the image: clients/ and deploy/ are pulled from it before
# every command; a job command (seed, onboard, sign-off, release, eval, baseline) runs on the job's work and the client's landing
# pulled from it, and puts its work back whether it succeeds or fails (seed its landing too; release publishes the
# released graph and names it current); serve fetches the current release into build/ before it serves; passwords puts the hashes and the
# passwords in the store's secrets/<client>/. Three commands are the store's alone, and need STORE_URL:
#
#   setup-client  CLIENT=<id> PEOPLE="<principal>:<role> ..." [ASKER=<principal>] [BASELINE=<path under clients/<id>/>]:
#                 the client's people and its profile in the store's deploy/, once per client
#   put-example   [EXAMPLE=<name>] [FROM=<folder>]: example content, a pack, put into the store's clients/ (default the
#                 kit's invented example, meridian, from /kit/examples)
#   releases      the client's releases in the store, current marked
#
# Sign-in. The caller of a container is never on its machine (it calls from the container network's address, never
# loopback), so demo mode cannot sign anyone in there: serve sets SIGNIN=passwords unless it is set, the pilot's sign-in,
# a username and a password per person. The passwords command makes them, into the profile's run state in build/, the
# volume: before serve, in a container of its own (docker run ... <image> passwords), or beside it (docker exec
# <container> kge passwords), with no restart; passwords.txt there holds them, for you alone (the README's Running it in
# a container). A bearer token signs in as ever: python -m kge.services.identity.cli in the container (docker exec
# <container> python -m kge.services.identity.cli) prints each principal's, kept in the same run state.
set -eu
cd "$(dirname "$0")"
usage() {
    echo "usage: entrypoint.sh serve|seed|reload|lock|onboard|onboard-request|improve|sign-off|release|eval|baseline|passwords|test-drive [NAME=value ...], for PROFILE (else the registry's default)" >&2
    echo "       entrypoint.sh setup-client|put-example|put-baseline|releases [NAME=value ...], with STORE_URL, the object store" >&2
    echo "serve signs people in with SIGNIN=passwords: kge passwords makes each person's, in build/ (docker run ... <image> passwords)" >&2
    exit 2
}
[ $# -gt 0 ] || usage
command=$1
shift
for setting in "$@"; do
    if [ "$command" = test-drive ] || [ "$command" = put-baseline ]; then
        case $setting in CLIENT=*|PROFILE=*|EXAMPLE=*) echo "$command takes its client from the install, not a CLIENT, PROFILE or EXAMPLE parameter" >&2; exit 2 ;; esac
    fi
    case $setting in
        [A-Za-z_]*=*) export "$setting" ;;   # onboard's own hash, and the profile, are read with it
        *) usage ;;
    esac
done
store() { "${PY:-python3}" -m kge.pipeline.objstore "$@"; }
# A job command's last step: run, then put its work back when there is a store. Keep the command's
# failure even if the put fails too; a failed put changes success to failure. release is the exception in the table below:
# after its atomic local current swap, a later sync may fail, and saving then could publish that failed release.
run() {
    command_status=0
    "$@" || command_status=$?
    if { [ "$command" = onboard ] || [ "$command" = reload ]; } && [ "$command_status" -ne 0 ] && [ -f "$MARGIN_STATUS" ]; then
        margin_status=$(cat "$MARGIN_STATUS" 2>/dev/null || true)
        case $margin_status in 1|2) command_status=$margin_status ;; esac
    fi
    if [ "$command" = onboard ] || [ "$command" = reload ]; then rm -f "$MARGIN_STATUS"; fi
    after_status=0
    if [ -n "${STORE_URL:-}" ]; then
        "${PY:-python3}" -m kge.pipeline.objstore after "$command" || after_status=$?
    fi
    if [ "$command_status" -ne 0 ]; then exit "$command_status"; fi
    exit "$after_status"
}
run_success_only() {
    if [ -z "${STORE_URL:-}" ]; then exec "$@"; fi
    "$@"
    exec "${PY:-python3}" -m kge.pipeline.objstore after "$command"
}
onboard_job() {
    export TAGGER_PROVIDER="${TAGGER_PROVIDER:-rules}"
    kge check compile "$@" || return
    artifact=$("${PY:-python3}" -m kge.compiler.compile --check) || return
    h="$(basename "$artifact")"
    said=" $("${PY:-python3}" -m kge.pipeline.profile --make --hash "$h" 2>/dev/null || true) "
    case $said in *" loaded=yes "*)
        case $said in *" current=$h "*) echo "onboard: current already"; return 0 ;; esac
        echo "onboard: candidate already loaded=$h"
        kge margin HASH="$h" "$@"
        return ;;
    esac
    kge load HASH="$h" "$@" || return
    kge margin HASH="$h" "$@"
}
reload_job() {
    said=$("${PY:-python3}" -m kge.pipeline.profile --make) || return
    h=
    for word in $said; do case $word in current=*) h=${word#current=} ;; esac; done
    [ -n "$h" ] || { echo "reload: no current release" >&2; return 2; }
    export TAGGER_PROVIDER="${TAGGER_PROVIDER:-rules}"
    kge load "$@" HASH="$h" || return
    kge margin "$@" HASH="$h"
}
prepare_margin_status() {
    said=$("${PY:-python3}" -m kge.pipeline.profile --make 2>/dev/null || true)
    runs=
    for word in $said; do case $word in runs=*) runs=${word#*=} ;; esac; done
    [ -n "$runs" ] || return 2
    MARGIN_STATUS="$(dirname "$runs")/margin.status"
    export MARGIN_STATUS
    rm -f "$MARGIN_STATUS"
}
baseline_job() {
    said=$("${PY:-python3}" -m kge.pipeline.profile --make)
    word() { for w in $said; do case $w in "$1="*) echo "${w#*=}" ;; esac; done; }
    graphfile=$(word graphfile)
    file="${graphfile#graph.}-$(date -u +%Y%m%dT%H%M%SZ).json"
    runs=$(word runs)
    (
        export MODEL_PROVIDER=none TAGGER_PROVIDER=rules
        export MODEL_HIGH_PROVIDER=none MODEL_MEDIUM_PROVIDER=none MODEL_LOW_PROVIDER=none
        unset MODEL_HIGH_NAME MODEL_MEDIUM_NAME MODEL_LOW_NAME MOCK_MODE
        kge ab "$@"
    ) || return
    run_id=$(ls -t "$runs" | head -n 1) || return
    kge baseline RUN="$run_id" BASELINE="$(word pack)/baselines/$file" NAME="recorded on ${graphfile#graph.}, run $run_id" "$@"
}
case $command in
    test-drive)
        exec "${PY:-python3}" -m kge.services.onboard.test_drive ;;
    setup-client|put-example|put-baseline|releases|onboard-request)
        if [ -z "${STORE_URL:-}" ]; then echo "$command needs STORE_URL, the object store (kge/pipeline/objstore.py)" >&2; exit 2; fi
        if [ "$command" = put-example ]; then EXAMPLE=${EXAMPLE:-meridian}; export EXAMPLE; fi
        exec "${PY:-python3}" -m kge.pipeline.objstore "$command" ;;
    serve|seed|reload|lock|onboard|improve|sign-off|release|eval|baseline|passwords|reset-overlay)
        if [ -n "${STORE_URL:-}" ]; then store before "$command"; fi ;;
    *) usage ;;
esac
case $command in
    serve)
        SIGNIN=${SIGNIN:-passwords}
        export SIGNIN
        if [ -n "${API_URL:-}" ]; then set -- API="$API_URL" "$@"; fi
        exec kge serve HOST=0.0.0.0 "$@" ;;
    seed) run kge estate "$@" ;;
    lock) run kge lock "$@" ;;
    reset-overlay) run kge reset-overlay "$@" ;;
    onboard)
        prepare_margin_status
        units=clients
        if [ -n "${STORE_URL:-}" ]; then units="clients/$(store client)/units"; fi
        if find "$units" -path '*/units/*' -type f -print -quit 2>/dev/null | grep -q .; then
            run kge onboard "$@"
        fi
        run onboard_job "$@" ;;
    reload)
        prepare_margin_status
        run reload_job "$@" ;;
    improve) run kge improve "$@" ;;
    sign-off) run kge sign-off "$@" ;;
    release) run_success_only kge release "$@" ;;
    eval) run kge ab "$@" ;;
    baseline)
        baseline_status=0
        baseline_job "$@" || baseline_status=$?
        after_status=0
        if [ -n "${STORE_URL:-}" ]; then "${PY:-python3}" -m kge.pipeline.objstore after "$command" || after_status=$?; fi
        if [ "$baseline_status" -ne 0 ]; then exit "$baseline_status"; fi
        if [ "$after_status" -ne 0 ]; then exit "$after_status"; fi
        echo "baseline: baselines/$file, from run $run_id"
        echo "next: point the profile at it, keeping every person and the asker it has:"
        "${PY:-python3}" -m kge.pipeline.objstore setup-client-line "baselines/$file" ;;
    passwords) run kge passwords "$@" ;;
esac
