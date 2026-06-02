#!/bin/bash
F=/opt/vfs-monitor/data/datewatch_de.json
age=$(( $(date +%s) - $(stat -c %Y "$F" 2>/dev/null || echo 0) ))
act=$(systemctl is-active vfs-datewatch 2>/dev/null)
if [ "$act" != active ] || [ "$age" -gt 1800 ]; then
  systemctl restart vfs-datewatch
  echo "$(date '+%F %T') WATCHDOG restart act=$act age=${age}s" >> /opt/vfs-monitor/data/watchdog.log
fi