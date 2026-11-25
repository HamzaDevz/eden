#!/bin/zsh
# folj.sh : Brothership repro of the burnt image (24/11). On the save copy started by trk.sh start (wait ~45 s
# for the title), loads slot 1 and walks Mario to the cannon, opens it; then aim at the island by hand
# (step.sh 0 150 0.5 +-20000 0 until "Zoom" shows), A to zoom, A x6 to land on Foljungle.
D=${0:A:h}
s() { zsh $D/step.sh "$@"; }
s 1 120 4; s 1 120 2; s 1 120 12
s 0 1500 0.5 25000 20000
s 1 300 0.1 0 30000; s 0 1200 0.6 0 30000
s 1 120 3
