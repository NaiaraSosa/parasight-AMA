#!/usr/bin/env bash

read -p "Usuario de rho: " RHO_USER

echo
echo "Se va a abrir el tunel SSH."
echo "Si pide password, escribilo aunque no se vean caracteres."
echo "Deja esta terminal abierta mientras uses Parasight."
echo

ssh -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -N -L 8010:127.0.0.1:8010 "$RHO_USER@10.1.103.91"