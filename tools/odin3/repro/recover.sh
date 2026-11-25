#!/bin/zsh
# recover.sh : after route.sh, while the field hint is still visible, walk toward the two enemies right of the rock
D=${0:A:h}
s() { zsh $D/step.sh "$@" >/dev/null; }
for m in "0 600 0.2 0 -30000" "0 2500 0.3 30000 0" "0 2000 0.3 0 30000" "0 2500 0.3 30000 15000" \
         "1 500 0.1 30000 15000" "0 2500 0.3 30000 25000" "0 2500 0.3 -10000 30000" "0 3000 0.3 30000 -10000"; do
  python3 $D/infield.py $D/tvs.png || { echo battle; exit 0; }
  s ${=m}
  cp $D/tvs.png $D/rec_$((++i)).png
done
python3 $D/infield.py $D/tvs.png && { echo field; exit 1; }
echo battle
