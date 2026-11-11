#!/data/data/com.termux/files/usr/bin/sh
# Run by Eden (TermuxHook) every minute while a game plays. Android kills Termux whole when the
# game needs its memory (11/11 at 12:16 and 13:59: no more AIs, nor points), so this starts the
# AIs' loop again when it is gone, as this Termux task itself, which keeps Termux in the
# foreground.
pgrep -f termux_ultimus.sh >/dev/null && exit 0
echo "$(date '+%F %T') Termux restarted by Eden" >> ~/smash-ai/ultimus.log
exec sh ~/odin3/termux_ultimus.sh >/dev/null 2>&1 </dev/null
