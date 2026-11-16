#!/usr/bin/env bash
# Serialize rebuild evaluation with installation of the deployed tree.
set -euo pipefail
exec flock -x /run/lock/nixos-lf-deploy.lock nixos-rebuild "${1:-switch}" --flake /etc/nixos#lfour "${@:2}"
