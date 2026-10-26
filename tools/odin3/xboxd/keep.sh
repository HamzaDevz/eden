#!/system/bin/sh
# Restarts xboxd whenever it exits, keeping its log across restarts
D=/data/adb/odin3-xbox
L=/data/local/tmp/odin3-xboxd.log
while :; do
    [ "$(stat -c %s $L 2>/dev/null || echo 0)" -gt 1048576 ] && mv $L $L.1
    echo "$(date '+%m-%d %H:%M:%S') I Launching xboxd" >> $L
    chmod 644 $L
    $D/xboxd $D >> $L 2>&1 < /dev/null
    echo "$(date '+%m-%d %H:%M:%S') E xboxd exited with $?" >> $L
    sleep 2
done
