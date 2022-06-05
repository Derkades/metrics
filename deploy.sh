#!/bin/bash
set -e
docker buildx build -t derkades/metrics --push .
