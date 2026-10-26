sh -c 'pkill -f "odin3-xbox/kee[p]"; pkill -x xboxd; sleep 1'
sh -c 'D=/data/adb/odin3-xbox; mkdir -p $D && cp /data/local/tmp/odin3-xbox/xboxd /data/local/tmp/odin3-xbox/keep.sh $D/ && chown -R root:root $D && chmod 700 $D $D/xboxd $D/keep.sh'
sh -c 'cp /data/media/0/Android/data/com.miHoYo.Yuanshen.relWithDebInfo/files/xbox_dongle/xone_dongle_*.bin /data/adb/odin3-xbox/'
sh -c 'setsid /data/adb/odin3-xbox/keep.sh < /dev/null > /dev/null 2>&1 & sleep 1'
