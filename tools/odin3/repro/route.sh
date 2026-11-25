#!/bin/zsh
# route.sh : from the field start after the intro (infield.py sees the « Se déplacer / Sauter » hint), walk into
# the first battle; the encounter (« ! » over two onions) starts the battle a few seconds later
D=${0:A:h}
s() { zsh $D/step.sh "$@" >/dev/null; }
s 0 1500 1 12000 25000
s 0 3000 0.5 15000 30000
s 0 1500 0.3 30000 0; s 0 2000 0.3 0 30000
s 1 700 0.2 0 30000; s 0 1200 0.3 0 30000
s 0 2500 0.3 -30000 0
s 0 900 1.5 -30000 -8000
s 0 1200 0.2 -20000 25000; s 1 600 0.2 -20000 25000; s 0 1500 0.3 -20000 25000
s 1 500 0.1 15000 30000; s 0 2500 0.3 15000 30000
s 0 1300 0.1 30000 5000; s 0 2000 0.3 12000 30000
s 0 3000 0.3 30000 0
# the route above stops behind the rock under the enemies' ledge (25/11, internal screen and TV alike); go back
# left, up to the round terrace by the water, up-right past the "?" block, then right into the two onions
s 0 2000 0.1 -30000 0; s 0 1500 0.3 0 30000
s 1 500 0.1 15000 30000; s 0 2500 0.3 15000 30000
s 0 1300 0.1 30000 5000; s 0 2000 0.1 12000 30000; s 0 2000 0.4 30000 0
