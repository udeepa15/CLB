#!/usr/bin/env bash

echo "# Network devices"
ip link list

echo -e "\n# Route table"
ip route list

echo -e "\n# iptables rules"
# Added the command below to actually list the rules
sudo iptables -L -n -v