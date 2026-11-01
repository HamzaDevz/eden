#!/data/data/com.termux/files/usr/bin/sh
# The AIs on the Odin itself, without the Mac: run by Termux:Boot (~/.termux/boot/), this plays
# their FIG cards whenever Smash is open. ~/smash-ai links to /sdcard/smash-ai, where the Mac
# pushes the models (smash_bc.py npz) through adb and touches "pause" every minute while it
# trains Ultimus against the CPU on the bridge; a pause it left more than 5 minutes ago is ignored.
sshd 2>/dev/null
cd ~/odin3 || exit 1
while true; do
  if [ -z "$(find ~/smash-ai/pause -mmin -5 2>/dev/null)" ] &&
     python -c "import socket; socket.create_connection(('127.0.0.1', 6560), 2)" 2>/dev/null; then
    # With ~/smash-ai/train (the Odin left to them), they also start each match and pass the
    # results; a press on someone else's controller stops that and gives the Odin back (the
    # recorder removes ~/smash-ai/train and quits a match it started). Everyone's Smash points
    # (from 1,000,000 per mode as Smash's GSP, worked out after each match against people) are
    # kept by name, shown after each person's name tag and drawn by Eden over each card on the
    # character select.
    python smash_record.py --players 8 --pad 7 --figs Ultimus,Optimus,Nostradamus,Magnus \
      --agent ~/smash-ai/models/ultimus.npz --sparring --min-battery 0 --sync 1 \
      --points ~/smash-ai/points.json --badges ~/smash-ai/badges-points.json \
      $([ -e ~/smash-ai/train ] && echo --auto) >> ~/smash-ai/ultimus.log 2>&1
  fi
  sleep 30
done
