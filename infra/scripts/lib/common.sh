# shellcheck shell=bash
# Shared by install.sh, install-monitoring.sh, update.sh and recette.sh (sourced from infra/,
# never run).

step() { printf '\n== %s\n' "$1"; }
fail() { echo "Error: $1" >&2; exit 1; }

# set_env KEY VALUE: the KEY line of infra/.env, replaced or appended (values without `|`).
set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

# env_value KEY: the value of KEY in infra/.env (empty when absent).
env_value() { sed -n "s/^$1=//p" .env 2>/dev/null | tail -1; }

# yaml_string VALUE: VALUE as a quoted YAML (and JSON) string, whatever it contains.
yaml_string() { jq -Rn --arg v "$1" '$v'; }

# wait_services: the services of this installation that report their health and keep
# running, once their containers exist (after `docker compose up -d`): a Compose health check,
# or the image's HEALTHCHECK. Docker Compose 2.x refuses `up --wait` on any other ("has no
# healthcheck configured": the scheduler, Traefik) and one-shot services (migrate) exit.
wait_services() {
  local ids format
  mapfile -t ids < <(docker compose ps -aq)
  [[ "${#ids[@]}" -gt 0 ]] || return 0
  # shellcheck disable=SC2016  # a Go template, not shell
  format='{{$restart := .HostConfig.RestartPolicy.Name}}'
  # shellcheck disable=SC2016
  format+='{{if and .State.Health (ne $restart "no") (ne $restart "")}}'
  format+='{{index .Config.Labels "com.docker.compose.service"}}{{end}}'
  docker inspect --format "$format" "${ids[@]}" | sed '/^$/d' | sort -u
}
