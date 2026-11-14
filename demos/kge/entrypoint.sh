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
#   onboard    kge onboard: rebuild a changed fixture, refresh the lock, compile, load and check the candidate;
#              a rebuilt estate reloads even the current hash; an unchanged current is left alone
#   onboard-request  validate and run the page's store-backed request (the Cloud Run job's default command)
#   sign-off   kge sign-off PRINCIPAL=<steward>
#   release    kge release; the next serve serves it; record its baseline and update the profile
#   eval       kge ab, the A/B harness
#   tagger-eval kge tagger-eval: passage scoring, LIVE=yes for configured tiers, AB=yes for answering A/B;
#              results in runs/<client>/tagger-eval/<run>/ in the store when STORE_URL is set
#   baseline   the profile's own A/B baseline, recorded on this setup: kge ab, then kge baseline on that run into the
#              pack's baselines/<graph store>-<UTC time>.json (with a store, put in its clients/<client>/baselines/); it
#              updates the profile to name it, keeping the people and asker
#   passwords  kge passwords: a password per person, in the run state (with a store, put in it: below)
#
# The object store (kge/adapters/objstore/, its commands kge/pipeline/objstore.py). With STORE_URL unset, each command is today's kge commands and nothing else.
# With it set, the store holds what is a client's, never the image: clients/ and deploy/ are pulled from it before
# every command; a job command (seed, onboard, sign-off, release, eval, tagger-eval, baseline) runs on the job's work and the client's landing
# pulled from it. Onboard publishes only on success; a failure saves its diagnostic run and report. Release publishes
# only on success, then records its baseline; seed puts its landing too. Other jobs put their work back after running.
# Serve fetches the current release into build/ before it serves; passwords puts the hashes and the
# passwords in the store's secrets/<client>/. Store commands need STORE_URL:
#
#   setup-client  CLIENT=<id> [ASKER=<role>] [BASELINE=<path under clients/<id>/>]:
#                 the client's profile in the store's deploy/, preserving existing values
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
    echo "usage: entrypoint.sh serve|seed|reload|lock|onboard|onboard-request|improve|sign-off|release|eval|tagger-eval|baseline|passwords|access|test-drive|check-pack [NAME=value ...], for PROFILE (else the registry's default)" >&2
    echo "       entrypoint.sh teardown|setup-client|put-example|put-baseline|releases [NAME=value ...], with STORE_URL, the object store" >&2
    echo "serve signs people in with SIGNIN=passwords: kge passwords makes each person's, in build/ (docker run ... <image> passwords)" >&2
    exit 2
}
[ $# -gt 0 ] || usage
command=$1
shift
# PACK= on the command line is the pack to check. A PACK already in the environment is not.
pack_argument=
for setting in "$@"; do
    if [ "$command" = test-drive ] || [ "$command" = put-baseline ]; then
        case $setting in CLIENT=*|PROFILE=*|EXAMPLE=*) echo "$command takes its client from the install, not a CLIENT, PROFILE or EXAMPLE parameter" >&2; exit 2 ;; esac
    fi
    case $setting in
        PACK=*) pack_argument=$setting; export "$setting" ;;
        [A-Za-z_]*=*) export "$setting" ;;   # onboard's own hash, and the profile, are read with it
        *) usage ;;
    esac
done
case $command in
    onboard|release|sign-off|passwords|seed|teardown|onboard-request)
        "${PY:-python3}" -m kge.pipeline.attempt "$command" ;;
esac
store() { "${PY:-python3}" -m kge.pipeline.objstore "$@"; }
after() {
    case "$1" in
        release) kge _stored-after "COMMAND=$1" ;;
        onboard)
            # A portal catalogue publish writes its own ledger and has no graph run record.
            if [ "${CATALOGUE_TARGET:-}" = portal ]; then
                store catalogue-after
            else
                kge _stored-after "COMMAND=$1"
            fi
            ;;
        *) store after "$1" ;;
    esac
}
# A job command's last step: run, then publish on success for onboard, or save only its failure record.
# Keep the command's failure even if the put fails too; a failed put changes success to failure. Release uses
# run_success_only below so a failed local release never publishes its current swap.
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
        if [ "$command_status" -ne 0 ] && [ "$command" = onboard ]; then
            store failed "$command" || after_status=$?
        else
            after "$command" || after_status=$?
        fi
    fi
    if [ "$command_status" -ne 0 ]; then exit "$command_status"; fi
    exit "$after_status"
}
run_success_only() {
    success_status=0
    "$@" || success_status=$?
    if [ "$success_status" -eq 3 ] && [ "$command" = release ]; then
        if [ -n "${STORE_URL:-}" ]; then after release; fi
        echo "EVAL POST FAILED: the release stays served; run kge rollback" >&2
        return 3
    fi
    if [ "$success_status" -ne 0 ]; then
        if [ -n "${STORE_URL:-}" ]; then store failed "$command" || true; fi
        return "$success_status"
    fi
    if [ -n "${STORE_URL:-}" ]; then after "$command"; fi
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
    said=$("${PY:-python3}" -m kge.pipeline.profile --make) || return
    word() { for w in $said; do case $w in "$1="*) echo "${w#*=}" ;; esac; done; }
    graphfile=$(word graphfile)
    file="${graphfile#graph.}-$(date -u +%Y%m%dT%H%M%SZ).json"
    runs=$(word runs)
    [ -n "$runs" ] && [ -n "$(word pack)" ] && [ -n "$graphfile" ] || {
        echo "baseline: profile has no pack, graph store or runs folder" >&2; return 2;
    }
    (
        export MODEL_PROVIDER=none TAGGER_PROVIDER=rules
        export MODEL_HIGH_PROVIDER=none MODEL_MEDIUM_PROVIDER=none MODEL_LOW_PROVIDER=none
        unset MODEL_HIGH_NAME MODEL_MEDIUM_NAME MODEL_LOW_NAME MOCK_MODE
        kge ab "$@" BASELINE=
    ) || return
    [ -d "$runs" ] || { echo "baseline: the harness wrote no runs folder" >&2; return 2; }
    run_id=$(ls -t "$runs" | head -n 1) || return
    [ -n "$run_id" ] || { echo "baseline: the harness wrote no run" >&2; return 2; }
    kge baseline "$@" RUN="$run_id" BASELINE="$(word pack)/baselines/$file" NAME="recorded on ${graphfile#graph.}, run $run_id" || return
    if [ -n "${STORE_URL:-}" ]; then
        "${PY:-python3}" -m kge.pipeline.registry set-baseline "baselines/$file" || return
        after baseline || return
    else
        "${PY:-python3}" - "$file" <<'PYBASE'
import sys
import yaml
from kge.pipeline.profile import resolve
prof = resolve()
registry = yaml.safe_load(prof.registry.read_text())
registry["profiles"][prof.id]["baseline"] = str(prof.clients / prof.client / "baselines" / sys.argv[1])
prof.registry.write_text(yaml.safe_dump(registry, sort_keys=False))
PYBASE
    fi
}
case $command in
    check-pack)
        # Forward only a PACK= argument, including a blank one, which the check refuses.
        # A PACK already in the environment is not read. With no argument the image checks
        # its profile, or refuses when it has no registry.
        if [ -n "$pack_argument" ]; then
            exec kge check-pack "$pack_argument"
        fi
        exec kge check-pack ;;
    access)
        if [ -n "${STORE_URL:-}" ]; then store before access; fi
        exec "${PY:-python3}" -m kge.services.access.cli ;;
    test-drive)
        exec "${PY:-python3}" -m kge.services.onboard.test_drive ;;
    teardown|setup-client)
        if [ -z "${STORE_URL:-}" ]; then echo "$command needs STORE_URL" >&2; exit 2; fi
        if [ "$command" = teardown ]; then exec "${PY:-python3}" -m kge.pipeline.teardown; fi
        exec "${PY:-python3}" -m kge.pipeline.registry setup-client ;;
    put-example|put-baseline|releases|onboard-request)
        if [ -z "${STORE_URL:-}" ]; then echo "$command needs STORE_URL, the object store (kge/pipeline/objstore.py)" >&2; exit 2; fi
        if [ "$command" = put-example ]; then EXAMPLE=${EXAMPLE:-meridian}; export EXAMPLE; fi
        exec "${PY:-python3}" -m kge.pipeline.objstore "$command" ;;
    serve|seed|reload|lock|onboard|improve|sign-off|release|eval|tagger-eval|eval-pre|baseline|passwords|reset-overlay)
        if [ -n "${STORE_URL:-}" ]; then store before "$command"; fi ;;
    *) usage ;;
esac
case $command in
    serve)
        SIGNIN=${SIGNIN:-passwords}
        export SIGNIN
        if [ -n "${API_URL:-}" ]; then set -- API="$API_URL" "$@"; fi
        exec kge serve HOST=0.0.0.0 "$@" ;;
    seed) run_success_only kge estate "$@" ;;
    lock) run kge lock "$@" ;;
    reset-overlay) run kge reset-overlay "$@" ;;
    onboard)
        prepare_margin_status
        export TAGGER_PROVIDER="${TAGGER_PROVIDER:-rules}"
        run kge onboard "$@" ;;
    reload)
        prepare_margin_status
        run reload_job "$@" ;;
    improve) run kge improve "$@" ;;
    sign-off) run kge sign-off "$@" ;;
    release)
        run_success_only kge release "$@"
        released_hash=$("${PY:-python3}" -m kge.pipeline.profile --make | tr ' ' '\n' | sed -n 's/^current=//p')
        baseline_status=0
        if [ -n "${STORE_URL:-}" ]; then store before baseline || baseline_status=$?; fi
        if [ "$baseline_status" -eq 0 ]; then baseline_job BASELINE= || baseline_status=$?; fi
        if [ "$baseline_status" -ne 0 ]; then
            if [ -n "${STORE_URL:-}" ]; then store failed baseline || true; fi
            echo "release: $released_hash is released; recording its baseline failed: exit $baseline_status: run baseline" >&2
            exit "$baseline_status"
        fi
        echo "release: baseline re-recorded: baselines/$file, from run $run_id; the profile names it, people and asker kept" ;;
    eval)
        if [ -n "${SUITE:-}" ] || [ -n "${RUNTIMES:-}" ] || [ -n "${ARM:-}" ]; then
            run kge eval "$@"
        else
            run kge ab "$@"
        fi ;;
    tagger-eval) run kge tagger-eval "$@" ;;
    eval-pre) run kge eval-pre "$@" ;;
    baseline)
        baseline_status=0
        baseline_job "$@" || baseline_status=$?
        if [ "$baseline_status" -ne 0 ]; then
            if [ -n "${STORE_URL:-}" ]; then store failed baseline || true; fi
            exit "$baseline_status"
        fi
        echo "baseline: baselines/$file, from run $run_id; the profile names it, people and asker kept" ;;
    passwords) run kge passwords "$@" "ROTATE=${ROTATE:-}" ;;
esac
