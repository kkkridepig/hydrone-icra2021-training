#!/usr/bin/env bash
# Sequential acceptance tests only; fail immediately, never start full training.
set -eo pipefail
server_ws="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
for stage in 1 2; do
  for algorithm in ddpg sac; do
    for steps in 1 100 1000; do
      bash "$server_ws/tools/server/run.bash" --algorithm "$algorithm" --stage "$stage" --steps "$steps"
    done
    bash "$server_ws/tools/server/run.bash" --algorithm "$algorithm" --stage "$stage" --episodes 2
  done
done
echo ALL_SERVER_GATES_PASSED
