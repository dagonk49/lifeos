#!/usr/bin/env bash
# Acceptance run on the target machine (docs/recette-serveur.md): times and memory peaks of the
# main functions with the resources Compose allocates, one by one then all at once, in a report
# without personal data (infra/state/recette/<UTC time>/rapport.md).
#
#   infra/scripts/recette.sh [options]
#
# Phases: idle; local OCR (receipt photos, 3-page scans); semantic indexing (embeddings);
# transcription; HTTPS and exposure; with a browser session: search, a document read through the
# API and the worker, the assistant; then everything at once (--load-seconds).
# Inputs are generated (receipts, scans, passages) or chosen by the owner (--audio: a reading of
# a public text): nothing personal goes in. Only durations, statuses, sizes and memory come out.
# Memory: `docker stats` every second per service and phase, the kernel's peak of each container
# (cgroup v2 memory.peak, short peaks included), OOM kills, restarts, the host's free memory.
#
# Options:
#   --rounds N           items per phase (default 3)
#   --load-seconds N     duration of the simultaneous load (default 90; 0: none)
#   --audio FILE         recording to transcribe (wav, mp3, ogg, webm, m4a), e.g. 1 minute of
#                        reading; default: a generated signal, which measures the model's loading
#                        and memory, not the transcription speed
#   --session-file FILE  value of the lifeos_session cookie of a browser session, in a file (never
#                        on the command line); log out of that browser afterwards
#   --chat N             assistant messages with a session (default 3, each one a Gemini request;
#                        0: none)
#   --out DIR            report directory
# Exit status: 0 every check passed; 1 a check failed (see the report); 2 bad usage.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/backup.sh
. scripts/lib/backup.sh
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
. scripts/lib/common.sh

rounds=3 load_seconds=90 audio="" session_file="" chat=3 out=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rounds) rounds="${2:?--rounds needs a value}"; shift 2 ;;
    --load-seconds) load_seconds="${2:?--load-seconds needs a value}"; shift 2 ;;
    --audio) audio="${2:?--audio needs a file}"; shift 2 ;;
    --session-file) session_file="${2:?--session-file needs a file}"; shift 2 ;;
    --chat) chat="${2:?--chat needs a value}"; shift 2 ;;
    --out) out="${2:?--out needs a directory}"; shift 2 ;;
    -h | --help) sed -n '2,31p' "$0"; exit 0 ;;
    *) echo "unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
done
[[ "$rounds" =~ ^[1-9][0-9]?$ ]] || { echo "--rounds: 1 to 99" >&2; exit 2; }
[[ "$load_seconds" =~ ^[0-9]{1,4}$ ]] || { echo "--load-seconds: 0 to 9999" >&2; exit 2; }
[[ "$chat" =~ ^[0-9]{1,2}$ ]] || { echo "--chat: 0 to 99" >&2; exit 2; }
[[ -z "$audio" || -r "$audio" ]] || die "cannot read $audio"
[[ -z "$session_file" || -r "$session_file" ]] || die "cannot read $session_file"
require_tools docker jq awk curl
[[ -f .env ]] || die "infra/.env is missing: install LifeOS first (install.sh)."

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="${out:-state/recette/$stamp}"
mkdir -p "$out"
timings="$out/timings.jsonl"  # one line per measurement: phase, op, status, seconds, sizes
memory="$out/memory.tsv"      # time, phase, service, MiB, CPU %
: > "$timings"
printf 'time\tphase\tservice\tmib\tcpu\n' > "$memory"
work="$(mktemp -d)"
helper="$(cat scripts/lib/recette.py)"
sampler=""
cleanup() {
  rm -f "$work/sampling"
  [[ -z "$sampler" ]] || wait "$sampler" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

# --- What runs ----------------------------------------------------------------------------
project="$(docker compose config --format json | jq -r .name)"
running() { [[ -n "$(docker compose ps -q --status running "$1" 2>/dev/null)" ]]; }
setting() {  # the value of a LifeOS setting in infra/.env, or its default
  local value
  value="$(env_value "$1")"
  echo "${value:-$2}"
}
in_service() {  # SERVICE ARGS...: the measurement helper inside a container
  local service="$1"
  shift
  docker compose exec -T "$service" python -c "$helper" "$@"
}
current_phase=repos
record() { jq --unbuffered -c --arg phase "$current_phase" '. + {phase: $phase}' >> "$timings"; }
phase() {
  current_phase="$1"
  echo "$1" > "$work/phase"
  step "$2"
}
skipped=()
skip() {  # CONSOLE_TEXT REPORT_TEXT: a measurement left out, said in the report (French)
  skipped+=("$2")
  echo "skipped: $1"
}

containers="$(docker compose ps -q)"
[[ -n "$containers" ]] || die "LifeOS is not running (docker compose up -d)."
mapfile -t ids <<<"$containers"
# container name -> service, memory limit, restarts and start time, before the run.
docker inspect --format \
  '{{.Name}}	{{index .Config.Labels "com.docker.compose.service"}}	{{.HostConfig.Memory}}	{{.RestartCount}}	{{.State.StartedAt}}	{{.Id}}' \
  "${ids[@]}" | sed 's|^/||' > "$work/before.tsv"
started_at="$(date +%s)"

# --- Memory sampler -----------------------------------------------------------------------
echo repos > "$work/phase"
touch "$work/sampling"
(
  while [[ -f "$work/sampling" ]]; do
    now="$(date +%s)"
    current="$(cat "$work/phase")"
    docker stats --no-stream --format '{{.Name}}	{{.MemUsage}}	{{.CPUPerc}}' "${ids[@]}" \
      2>/dev/null | awk -F'\t' -v t="$now" -v p="$current" -v map="$work/before.tsv" '
        function mib(s,   n, u) {
          n = s + 0; u = s; sub(/^[0-9.]+/, "", u)
          if (u == "GiB" || u == "GB") return n * 1024
          if (u == "MiB" || u == "MB") return n
          if (u == "KiB" || u == "kB" || u == "KB") return n / 1024
          return n / 1048576
        }
        BEGIN { while ((getline line < map) > 0) { split(line, f, "\t"); svc[f[1]] = f[2] } }
        { split($2, m, " / "); c = $3; sub(/%/, "", c)
          printf "%s\t%s\t%s\t%.0f\t%s\n", t, p, ($1 in svc ? svc[$1] : $1), mib(m[1]), c }' \
      >> "$memory" || true
    awk -v t="$now" -v p="$current" '/^MemAvailable:/ { a = $2 } /^SwapTotal:/ { st = $2 }
      /^SwapFree:/ { sf = $2 }
      END { printf "%s\t%s\t(host available)\t%.0f\t\n%s\t%s\t(host swap used)\t%.0f\t\n",
            t, p, a / 1024, t, p, (st - sf) / 1024 }' /proc/meminfo >> "$memory"
    sleep 1
  done
) &
sampler=$!

# --- Phases -------------------------------------------------------------------------------
phase repos "Idle (15 s)"
sleep 15

if running ml; then
  phase ocr "Local OCR: $rounds receipt photos and $rounds scans of 3 pages"
  in_service ml ocr "$rounds" < /dev/null | record
  if [[ "$(setting LIFEOS_EMBEDDINGS_PROVIDER disabled)" != disabled ]]; then
    phase indexation "Semantic indexing: $rounds batches of 32 passages"
    in_service ml embed "$rounds" < /dev/null | record
  else
    skip "indexing (LIFEOS_EMBEDDINGS_PROVIDER=disabled)" \
      "indexation (recherche par le sens désactivée : LIFEOS_EMBEDDINGS_PROVIDER=disabled)"
  fi
else
  skip "OCR and indexing (ml is not running)" "OCR et indexation (service ml arrêté)"
fi

stt=0
if running stt && [[ "$(setting LIFEOS_STT_PROVIDER disabled)" != disabled ]]; then
  stt=1
  phase transcription "Transcription: model loading, then $rounds dictations"
  in_service stt stt "$((rounds + 1))" < "${audio:-/dev/null}" | record
else
  skip "transcription (stt is not running or LIFEOS_STT_PROVIDER=disabled)" \
    "dictée (service stt arrêté, ou LIFEOS_STT_PROVIDER=disabled)"
fi

# HTTPS as the owner's devices reach it: the domain resolves to this machine's port 443 (or
# LIFEOS_PROBE_ADDRESS, a reverse proxy on another machine), the certificate is verified.
phase https "HTTPS and exposure"
domain="$(env_value LIFEOS_DOMAIN)"
[[ -n "$domain" ]] || die "LIFEOS_DOMAIN is not set in infra/.env."
base="https://$domain"
curl_opts=(--silent --show-error --max-time 60 --noproxy '*'
  --resolve "$domain:443:$(setting LIFEOS_PROBE_ADDRESS 127.0.0.1)")
for ca in blackbox/ca/*.pem; do [[ -f "$ca" ]] && curl_opts+=(--cacert "$ca"); done
timed() {  # OP CURL_ARGS...: one request, its status and duration (JSON line)
  local op="$1"
  shift
  curl "${curl_opts[@]}" -o /dev/null -w "{\"op\":\"$op\",\"status\":%{http_code},\"seconds\":%{time_total}}\n" \
    "$@" 2>/dev/null || echo "{\"op\":\"$op\",\"status\":0,\"seconds\":0}"
}
https_checks="$work/https.tsv"
: > "$https_checks"
https_check() { printf '%s\t%s\n' "$1" "$2" >> "$https_checks"; }
https_check_that() {  # TEXT COMMAND...: ok when the command succeeds
  local text="$1"
  shift
  if "$@"; then https_check "$text" ok; else https_check "$text" FAIL; fi
}
metrics_hidden() {
  [[ "$(curl "${curl_opts[@]}" -o /dev/null -w '%{http_code}' "$base/metrics" 2>/dev/null)" != 200 ]]
}
if curl "${curl_opts[@]}" -o /dev/null "$base/healthz" 2>/dev/null; then
  https_check "Certificat accepté (vérifié comme par un navigateur)" ok
else
  https_check "Certificat accepté (vérifié comme par un navigateur)" FAIL
  curl_opts+=(--insecure)  # the other measurements still run
fi
headers="$(curl "${curl_opts[@]}" -D - -o /dev/null "$base/login" 2>/dev/null | tr -d '\r')"
https_check_that "HSTS envoyé" grep -qi '^strict-transport-security: max-age=' <<<"$headers"
https_check_that "CSP avec nonce sur les pages" \
  grep -qi "^content-security-policy:.*'nonce-" <<<"$headers"
https_check_that "/metrics non publié" metrics_hidden
if command -v openssl >/dev/null; then
  end="$(openssl s_client -connect "$(setting LIFEOS_PROBE_ADDRESS 127.0.0.1):443" \
    -servername "$domain" </dev/null 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null \
    | sed 's/^notAfter=//')"
  if [[ -n "$end" ]]; then
    days=$(( ($(date -d "$end" +%s) - $(date +%s)) / 86400 ))
    [[ "$days" -ge 14 ]] && status=ok || status=FAIL
    https_check "Certificat valable encore $days jours (au moins 14)" "$status"
  fi
fi
for _ in $(seq 1 "$rounds"); do
  timed web_login "$base/login" | record
  timed api_healthz "$base/healthz" | record
done

# With a browser session: what the owner does through the API.
session=0
if [[ -n "$session_file" ]]; then
  cookie="$(tr -d '[:space:]' < "$session_file")"
  [[ "$cookie" =~ ^[A-Za-z0-9._~+/=-]{16,512}$ ]] || die "the session file holds no session cookie."
  (umask 077 && printf 'header = "Cookie: lifeos_session=%s"\nheader = "Origin: %s"\n' \
    "$cookie" "$base" > "$work/session.curl")
  unset cookie
  curl_opts+=(-K "$work/session.curl")
  spaces="$(curl "${curl_opts[@]}" "$base/v1/spaces" 2>/dev/null || true)"
  if jq -e 'type == "array" and length > 0' <<<"$spaces" >/dev/null 2>&1; then
    session=1
  else
    skip "API measurements (the session is not valid: sign in again and copy the cookie)" \
      "mesures par l'API (session refusée : se reconnecter puis recopier le cookie)"
  fi
else
  skip "API measurements (no --session-file)" "mesures par l'API (sans --session-file)"
fi
space="" ai_space=""
if [[ "$session" -eq 1 ]]; then
  space="$(jq -r '(map(select(.kind == "personal")) + .)[0].id' <<<"$spaces")"
  ai_space="$(jq -r 'map(select(.cloud_ai_enabled))[0].id // empty' <<<"$spaces")"
  phase api "Search, a document read by the worker, the assistant (through the API)"
  for _ in $(seq 1 "$rounds"); do
    timed api_search "$base/v1/search?q=recette%20courses&limit=10" | record
  done
fi
uuid() { cat /proc/sys/kernel/random/uuid; }
document_round() {  # a generated receipt, uploaded, read by the worker and ml, then deleted
  local file="$1" t0 id status=0 state=""
  t0="$(date +%s.%N)"
  id="$(curl "${curl_opts[@]}" -H "Idempotency-Key: $(uuid)" -F "file=@$file;type=image/jpeg" \
    -F "space_id=$space" -F purpose=document -F "title=Recette technique" \
    "$base/v1/documents" 2>/dev/null | jq -r '.id // empty')"
  if [[ -n "$id" ]]; then
    for _ in $(seq 1 300); do
      state="$(curl "${curl_opts[@]}" "$base/v1/documents/$id" 2>/dev/null | jq -r '.status // empty')"
      [[ "$state" == pending || "$state" == processing ]] || break
      sleep 1
    done
    curl "${curl_opts[@]}" -X DELETE -o /dev/null "$base/v1/documents/$id" 2>/dev/null || true
    [[ "$state" == ready ]] && status=200
  fi
  jq -nc --arg s "$(awk -v a="$t0" -v b="$(date +%s.%N)" 'BEGIN { printf "%.3f", b - a }')" \
    --argjson status "$status" --arg state "${state:-upload_failed}" \
    '{op: "api_document", status: $status, seconds: ($s | tonumber), state: $state}'
}
chat_rounds() {  # N messages in a throw-away conversation, deleted afterwards
  local count="$1" conversation answer
  conversation="$(curl "${curl_opts[@]}" -H 'Content-Type: application/json' \
    -d "{\"space_id\":\"$ai_space\",\"title\":\"Recette technique\"}" \
    "$base/v1/chat/conversations" 2>/dev/null | jq -r '.id // empty')"
  [[ -n "$conversation" ]] || { echo '{"op":"api_chat","status":0,"seconds":0}'; return; }
  for _ in $(seq 1 "$count"); do
    answer="$(curl "${curl_opts[@]}" -H 'Content-Type: application/json' \
      -H "Idempotency-Key: $(uuid)" -w '\n%{http_code} %{time_total}' \
      -d '{"content":"Recette technique : réponds seulement « ok »."}' \
      "$base/v1/chat/conversations/$conversation/messages" 2>/dev/null || true)"
    jq -nc --arg tail "$(tail -1 <<<"$answer")" --arg body "$(sed '$d' <<<"$answer")" '
      ($tail | split(" ")) as $t
      | ($body | fromjson? // {}) as $b
      | {op: "api_chat", seconds: ($t[1] | tonumber? // 0),
         status: (if $b.assistant_message != null then ($t[0] | tonumber) else 0 end),
         outcome: ($b.error.code // (if $b.assistant_message != null then "ok" else "error" end))}'
  done
  curl "${curl_opts[@]}" -X DELETE -o /dev/null "$base/v1/chat/conversations/$conversation" \
    2>/dev/null || true
}
if [[ "$session" -eq 1 ]]; then
  if running ml; then
    in_service ml receipt < /dev/null > "$work/receipt.jpg"
    document_round "$work/receipt.jpg" | record
  fi
  if [[ "$chat" -gt 0 && -n "$ai_space" && "$(setting LIFEOS_LLM_PROVIDER disabled)" != disabled ]]
  then
    chat_rounds "$chat" | record
  else
    skip "assistant (no LLM provider, no space with the AI allowed, or --chat 0)" \
      "assistant (pas de fournisseur LLM, aucun espace où l'IA est autorisée, ou --chat 0)"
  fi
fi

# Everything at once: two OCR clients, indexing, dictations, pages and API requests.
if [[ "$load_seconds" -gt 0 ]]; then
  phase charge "Everything at once for $load_seconds s"
  jobs_=()
  if running ml; then
    in_service ml loop ocr "$load_seconds" 0 < /dev/null | record & jobs_+=($!)
    in_service ml loop ocr "$load_seconds" 1 < /dev/null | record & jobs_+=($!)
    if [[ "$(setting LIFEOS_EMBEDDINGS_PROVIDER disabled)" != disabled ]]; then
      in_service ml loop embed "$load_seconds" < /dev/null | record & jobs_+=($!)
    fi
  fi
  if [[ "$stt" -eq 1 ]]; then
    in_service stt loop stt "$load_seconds" < "${audio:-/dev/null}" | record & jobs_+=($!)
  fi
  web_loop() {
    local deadline=$((SECONDS + load_seconds))
    while [[ "$SECONDS" -lt "$deadline" ]]; do
      timed web_login "$base/login"
      timed api_healthz "$base/healthz"
      [[ "$session" -eq 0 ]] || timed api_search "$base/v1/search?q=recette%20rendez-vous&limit=10"
    done
  }
  web_loop | record & jobs_+=($!)
  web_loop | record & jobs_+=($!)
  if [[ "$session" -eq 1 ]] && running ml; then
    document_round "$work/receipt.jpg" | record & jobs_+=($!)
  fi
  if [[ "$session" -eq 1 && "$chat" -gt 0 && -n "$ai_space" \
    && "$(setting LIFEOS_LLM_PROVIDER disabled)" != disabled ]]; then
    chat_rounds 1 | record & jobs_+=($!)
  fi
  for job in "${jobs_[@]}"; do wait "$job" || true; done
fi
phase fin "Settling (5 s)"
sleep 5
rm -f "$work/sampling"
wait "$sampler" 2>/dev/null || true
sampler=""
ended_at="$(date +%s)"

# --- Memory: peaks, limits, kernel peaks, OOM kills, restarts -----------------------------
docker inspect --format \
  '{{.Name}}	{{index .Config.Labels "com.docker.compose.service"}}	{{.HostConfig.Memory}}	{{.RestartCount}}	{{.State.StartedAt}}	{{.Id}}' \
  "${ids[@]}" 2>/dev/null | sed 's|^/||' > "$work/after.tsv" || true
docker events --since "$started_at" --until "$ended_at" --filter type=container \
  --filter event=oom --filter "label=com.docker.compose.project=$project" \
  --format '{{index .Actor.Attributes "com.docker.compose.service"}}' > "$work/oom.txt" || true
kernel_peak() {  # the kernel's memory peak of a container (MiB): cgroup v2, else v1
  local file
  for file in "/sys/fs/cgroup/system.slice/docker-$1.scope/memory.peak" \
    "/sys/fs/cgroup/docker/$1/memory.peak" \
    "/sys/fs/cgroup/memory/system.slice/docker-$1.scope/memory.max_usage_in_bytes" \
    "/sys/fs/cgroup/memory/docker/$1/memory.max_usage_in_bytes"; do
    [[ -r "$file" ]] && { awk '{ printf "%.0f", $1 / 1048576 }' "$file"; return; }
  done
  echo -
}
: > "$work/services.tsv"  # service, limit MiB, kernel peak MiB, restarts, OOM kills
while IFS=$'\t' read -r name service limit restarts started id; do
  after="$(awk -F'\t' -v n="$name" '$1 == n { print $4 "\t" $5 }' "$work/after.tsv")"
  restarted=0
  if [[ -z "$after" ]]; then
    restarted=1
  elif [[ "${after%%$'\t'*}" != "$restarts" || "${after#*$'\t'}" != "$started" ]]; then
    restarted=1
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' "$service" "$((limit / 1048576))" "$(kernel_peak "$id")" \
    "$restarted" "$(grep -cx "$service" "$work/oom.txt" || true)" >> "$work/services.tsv"
done < "$work/before.tsv"

# --- Report -------------------------------------------------------------------------------
report="$out/rapport.md"
failures=0
verdicts="$work/verdicts.md"
: > "$verdicts"
verdict() {  # STATUS TEXT
  local mark='✅'
  if [[ "$1" != ok ]]; then
    mark='❌'
    failures=$((failures + 1))
  fi
  printf -- '- %s %s\n' "$mark" "$2" >> "$verdicts"
}
verdict_unless() {  # LIST TEXT_IF_EMPTY TEXT_IF_NOT: ok when LIST is empty
  if [[ -z "$1" ]]; then verdict ok "$2"; else verdict fail "$3"; fi
}
errors="$(jq -s -r '[.[] | select(.status != 200 and .status != 201 and .status != 204)]
  | group_by(.op) | map("\(.[0].op) (\(length))") | join(", ")' "$timings")"
if [[ -z "$errors" ]]; then
  verdict ok "Toutes les mesures ont abouti ($(wc -l < "$timings") requêtes)."
else
  verdict fail "Échecs : $errors (voir timings.jsonl)."
fi
oom="$(awk -F'\t' '$5 > 0 { printf "%s%s (%s)", sep, $1, $5; sep = ", " }' "$work/services.tsv")"
verdict_unless "$oom" "Aucun processus tué faute de mémoire." "Tué(s) faute de mémoire : $oom."
restarted="$(awk -F'\t' '$4 > 0 { printf "%s%s", sep, $1; sep = ", " }' "$work/services.tsv")"
verdict_unless "$restarted" "Aucun service redémarré." \
  "Redémarré(s) pendant la recette : $restarted."
# Peak per service: the highest of docker stats and of the kernel's peak since the start.
awk -F'\t' 'NR == FNR { limit[$1] = $2; kernel[$1] = $3; next }
  FNR > 1 && $3 !~ /^\(/ { if ($4 > peak[$3]) peak[$3] = $4 }
  END { for (s in limit) { p = peak[s] + 0; if (kernel[s] != "-" && kernel[s] + 0 > p) p = kernel[s] + 0
          if (limit[s] > 0) printf "%s\t%d\t%d\t%.0f\n", s, p, limit[s], 100 * p / limit[s] } }' \
  "$work/services.tsv" "$memory" | sort > "$work/peaks.tsv"
tight="$(awk -F'\t' '$4 >= 90 { printf "%s%s (%s %%)", sep, $1, $4; sep = ", " }' "$work/peaks.tsv")"
verdict_unless "$tight" "Chaque service reste sous 90 % de sa limite mémoire." \
  "Au-delà de 90 % de la limite : $tight (docs/exploitation.md §7)."
total_mib="$(awk '/^MemTotal:/ { printf "%.0f", $2 / 1024 }' /proc/meminfo)"
min_available="$(awk -F'\t' '$3 == "(host available)" { if (m == "" || $4 < m) m = $4 } END { print m + 0 }' "$memory")"
if [[ "$min_available" -ge $((total_mib / 20)) ]]; then
  verdict ok "Mémoire disponible de la machine jamais sous 5 % (minimum $min_available Mio)."
else
  verdict fail "Mémoire disponible de la machine tombée à $min_available Mio (moins de 5 %)."
fi
while IFS=$'\t' read -r text status; do verdict "$status" "$text."; done < "$https_checks"

stats_table() {  # Markdown rows, in the order of the phases: phase, operation, count, errors,
  # min, median, p95, max (seconds)
  jq -s -r 'def q($p): (length * $p | ceil) - 1 | if . < 0 then 0 else . end;
    def r: . * 1000 | round / 1000;
    ["repos", "ocr", "indexation", "transcription", "https", "api", "charge"] as $order
    | group_by([.phase, .op]) | sort_by((.[0].phase as $p | $order | index($p)) // 99)[]
    | (map(.seconds) | sort) as $s
    | [.[0].phase, .[0].op, length,
       (map(select(.status != 200 and .status != 201 and .status != 204)) | length),
       ($s[0] | r), ($s[($s | q(0.5))] | r), ($s[($s | q(0.95))] | r), ($s[-1] | r)]
    | "| \(.[0]) | \(.[1]) | \(.[2]) | \(.[3]) | \(.[4]) | \(.[5]) | \(.[6]) | \(.[7]) |"' "$timings"
}
speeds() {  # derived rates: OCR seconds per page, transcription speed
  jq -s -r 'def phases: sort_by(.[0].phase | if . == "charge" then 1 else 0 end)[];
    (map(select(.op | startswith("ocr")) | select(.status == 200 and (.pages // 0) > 0))
      | group_by(.phase) | phases | "- OCR (\(.[0].phase)) : \((map(.seconds) | add) / (map(.pages) | add)
      | . * 100 | round / 100) s par page en moyenne."),
    (map(select(.op == "stt" and .status == 200 and (.audio_seconds // 0) > 0)) | group_by(.phase)
      | phases
      | "- Transcription (\(.[0].phase), \(.[0].device // "?")\(if .[0].generated_audio
        then ", signal généré" else "" end)) : \((map(.audio_seconds) | add) / (map(.seconds) | add)
        | . * 10 | round / 10) × le temps réel (parole détectée : \(map(.speech_seconds // 0) | add
        | round) s sur \(map(.audio_seconds) | add | round) s)."),
    (map(select(.op == "stt_with_model_load" and .status == 200))[:1][]
      | "- Première dictée, chargement du modèle compris : \(.seconds) s.")' "$timings"
}
gpu="aucun"
command -v nvidia-smi >/dev/null && gpu="$(nvidia-smi --query-gpu=name,memory.total \
  --format=csv,noheader 2>/dev/null | head -1)"
{
  echo "# Recette de LifeOS du $(date -u '+%Y-%m-%d %H:%M') UTC"
  echo
  echo "Rapport de \`infra/scripts/recette.sh\` (docs/recette-serveur.md). Entrées synthétiques ou"
  echo "choisies par le propriétaire ; aucune donnée personnelle, aucun nom de domaine ni adresse."
  echo
  echo "## Machine"
  echo
  echo "| | |"
  echo "|---|---|"
  echo "| Processeur | $(sed -n 's/^model name[[:space:]]*: //p' /proc/cpuinfo | head -1) |"
  echo "| vCPU | $(nproc) ($(grep -o -w 'avx2\|fma' /proc/cpuinfo | sort -u | paste -sd' ' || true)) |"
  echo "| Mémoire | $total_mib Mio, swap $(awk '/^SwapTotal:/ { printf "%.0f", $2 / 1024 }' /proc/meminfo) Mio |"
  echo "| GPU | $gpu |"
  echo "| Système | $(sed -n 's/^PRETTY_NAME="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' /etc/os-release \
    2>/dev/null), noyau $(uname -r) |"
  echo "| Docker | $(docker version --format '{{.Server.Version}}' 2>/dev/null), Compose $(docker compose version --short 2>/dev/null) |"
  echo "| LifeOS | $(git -C .. describe --tags --always 2>/dev/null || echo ?) (images $(setting LIFEOS_IMAGE_TAG local)) |"
  echo "| Surcouches | $(setting COMPOSE_FILE compose.yaml) |"
  echo "| Fonctions locales | embeddings $(setting LIFEOS_EMBEDDINGS_PROVIDER disabled), OCR $(setting LIFEOS_EXTRACTION_PROVIDER disabled), dictée $(setting LIFEOS_STT_PROVIDER disabled), LLM $(setting LIFEOS_LLM_PROVIDER disabled) |"
  echo "| Paramètres | --rounds $rounds, --load-seconds $load_seconds, audio $([[ -n "$audio" ]] && echo "fourni" || echo "généré"), session $([[ "$session" -eq 1 ]] && echo oui || echo non) |"
  echo
  echo "## Verdict"
  echo
  cat "$verdicts"
  for item in "${skipped[@]}"; do echo "- ⏭️ Non mesuré : $item."; done
  echo
  echo "## Temps (secondes)"
  echo
  echo "| Phase | Opération | Nombre | Échecs | Min | Médiane | p95 | Max |"
  echo "|---|---|---|---|---|---|---|---|"
  stats_table
  echo
  speeds
  echo
  echo "## Mémoire (Mio)"
  echo
  echo "Pointe : la plus haute de \`docker stats\` (chaque seconde) et du noyau (memory.peak,"
  echo "depuis le démarrage du conteneur)."
  echo
  echo "| Service | Limite | Pointe | % limite | Pointes par phase (docker stats) |"
  echo "|---|---|---|---|---|"
  while IFS=$'\t' read -r service peak limit percent; do
    phases="$(awk -F'\t' -v s="$service" 'NR > 1 && $3 == s { if ($4 > m[$2]) m[$2] = $4; if (!($2 in o)) { o[$2] = ++n; k[n] = $2 } }
      END { for (i = 1; i <= n; i++) printf "%s%s %d", (i > 1 ? ", " : ""), k[i], m[k[i]] }' "$memory")"
    echo "| $service | $limit | $peak | $percent % | $phases |"
  done < "$work/peaks.tsv"
  echo
  echo "Machine : mémoire disponible minimale $min_available Mio sur $total_mib Mio ; swap utilisé"
  echo "au plus $(awk -F'\t' '$3 == "(host swap used)" && $4 > m { m = $4 } END { print m + 0 }' "$memory") Mio."
  echo
  echo "Fichiers bruts : \`timings.jsonl\` (durées, statuts, tailles) et \`memory.tsv\`."
} > "$report"

step "Report"
echo "$report"
if [[ "$failures" -gt 0 ]]; then
  echo "$failures check(s) failed: see the report." >&2
  exit 1
fi
echo "Every check passed."
