sh -c 'pkill -x xboxd; sleep 1'
sh -c 'D=/data/adb/odin3-xbox; mkdir -p $D && cp /data/local/tmp/odin3-xbox/xboxd /data/media/0/Android/data/com.miHoYo.Yuanshen.relWithDebInfo/files/xbox_dongle/xone_dongle_*.bin $D/ && chown -R root:root $D && chmod 700 $D $D/xboxd'
sh -c 'setsid /data/adb/odin3-xbox/xboxd /data/adb/odin3-xbox > /data/local/tmp/odin3-xboxd.log 2>&1 < /dev/null & sleep 1; chmod 644 /data/local/tmp/odin3-xboxd.log'
