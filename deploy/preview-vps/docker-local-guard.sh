#!/usr/bin/env bash

pin_local_preview_docker() {
  local socket=/var/run/docker.sock
  unset DOCKER_HOST DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH DOCKER_CONFIG DOCKER_API_VERSION
  export DOCKER_HOST=unix:///var/run/docker.sock
  [[ -S "$socket" && ! -L "$socket" ]] || {
    echo "Expected a local non-symlink Docker Unix socket" >&2
    return 2
  }
  [[ "$(stat -c '%u:%F:%h' "$socket")" == "0:socket:1" ]] || {
    echo "Unexpected Docker socket ownership, type or link count" >&2
    return 2
  }
  [[ "$(docker info --format '{{.DockerRootDir}}')" == "/var/lib/docker" ]] || {
    echo "Unexpected Docker root directory" >&2
    return 2
  }
  [[ "$(docker info --format '{{.Name}}')" == "$(hostname -s)" ]] || {
    echo "Docker daemon name does not match the local VPS hostname" >&2
    return 2
  }
  [[ "$(docker info --format '{{.Swarm.LocalNodeState}}')" == "active" ]] || {
    echo "The expected local VPS Swarm is not active" >&2
    return 2
  }
}
