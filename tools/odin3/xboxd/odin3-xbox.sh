sh -c 'pkill -f "odin3-xbox/kee[p]"; pkill -x xboxd; for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do pidof xboxd > /dev/null || break; sleep 0.5; done'
sh -c 'D=/data/adb/odin3-xbox; S=/data/local/tmp/odin3-xbox; mkdir -p $D && cp $S/xboxd $D/xboxd.new && cp $S/keep.sh $D/keep.sh.new && mv -f $D/xboxd.new $D/xboxd && mv -f $D/keep.sh.new $D/keep.sh && chown -R root:root $D && chmod 700 $D $D/xboxd $D/keep.sh'
sh -c 'cp /data/media/0/Android/data/com.miHoYo.Yuanshen.relWithDebInfo/files/xbox_dongle/xone_dongle_*.bin /data/adb/odin3-xbox/'
sh -c 'setsid /data/adb/odin3-xbox/keep.sh < /dev/null > /dev/null 2>&1 & sleep 1'
