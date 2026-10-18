# Compatibilité Odin 3 : Eden (Switch)

Notre propre liste de compatibilité, tenue d'après nos tests sur l'AYN Odin 3 (Snapdragon 8 Elite, Adreno 830) avec notre build de la branche `odin3`. Chaque test met à jour la ligne du jeu : statut, mesure, date et commit testé.

Autres émulateurs : [Eden (Switch)](https://github.com/HamzaDevz/eden/blob/odin3/ODIN3_COMPAT.md) · [Azahar (3DS)](https://github.com/HamzaDevz/azahar/blob/odin3/ODIN3_COMPAT.md) · [Dolphin (GC/Wii)](https://github.com/HamzaDevz/dolphin/blob/odin3/ODIN3_COMPAT.md) · [Cemu (Wii U)](https://github.com/HamzaDevz/Cemu/blob/odin3/ODIN3_COMPAT.md) · [Vita3K (Vita)](https://github.com/HamzaDevz/Vita3K/blob/odin3/ODIN3_COMPAT.md) · [aPS3e (PS3)](https://github.com/HamzaDevz/aps3e/blob/odin3/ODIN3_COMPAT.md) · [Bachata S4 (PS4)](https://github.com/HamzaDevz/bachata-s4/blob/odin3/ODIN3_COMPAT.md) · [xemu (Xbox)](https://github.com/HamzaDevz/xemu/blob/odin3/ODIN3_COMPAT.md)

## Échelle

| Statut | Sens |
|---|---|
| 🟢 Parfait | vu en partie, plein régime, aucun défaut vu |
| 🔵 Jouable | vu en partie, défauts mineurs |
| 🟡 Problèmes | vu en partie, défauts gênants (fps, graphismes, son) |
| 🟠 Menus/intro | démarre, pas encore vu en partie |
| 🔴 Ne marche pas | crash, écran noir ou blocage |
| ⚪ Non testé | présent sur la console, jamais lancé |

## Comment on teste

Paquet `com.miHoYo.Yuanshen.relWithDebInfo` (« Eden », variante genshinSpoof), pilote Qualcomm système 512.800.72. Lancement comme Hamza depuis ES-DE (MAJ montées par `external_content_dirs`) ou par le banc `tools/odin3/run_variant.sh`. Mesure par `tools/odin3/measure.sh` (SurfaceFlinger sur la couche du jeu) puis `summarize.py` : fps, part des images sous 60 (`below60`), chutes ; `poll.sh` pour la puissance et les températures (sur chargeur, comparables entre elles seulement). Méthode détaillée : [docs/Odin3.md](docs/Odin3.md), configs par jeu : [tools/odin3/configs/](tools/odin3/configs/).

Global depuis le 17/10 au soir : NCE, GPU asynchrone, Turbo coupé, `vertex_input_dynamic_state=true`, `cpu_idle_spin_us=200`.

## Jeux

| Jeu | Statut | Vu jusqu'à | Mesure | Notes | Testé le | Build |
|---|---|---|---|---|---|---|
| Hollow Knight: Silksong `010013C00E930000` | 🟢 Parfait | début de partie | 60,0 fps, 0,1 % sous 60, 9,3 W, CPU 77 °C | aucun réglage par jeu ; a4212fd545 corrige un `vulkan.bin` tronqué qui effaçait les pipelines | 2026-10-17 | ef4712f59c |
| Luigi's Mansion 2 HD `010048701995E000` | 🟢 Parfait | bunker, mission A-1 | 60,0 fps, 0 chute, 4,1-5,2 W | cheat 60 fps (StevensND) requis, sinon 30 fps (plafond du jeu) ; vitesse vérifiée | 2026-10-17 | ef4712f59c |
| Mario vs. Donkey Kong `0100B99019412000` | 🟢 Parfait | niveau 8-3 | 60,0 fps, 0,1 % sous 60, 3,7 W, 57 °C | titre à 30 fps sur une vidéo, c'est normal ; L+R pour démarrer | 2026-10-17 | ef4712f59c |
| Super Mario Bros. Wonder `010015100B514000` | 🟢 Parfait | niveau « Sauts et rythme » | 60,0 fps, 0,1 % sous 60, 3,8 W | INI : GPU asynchrone, Turbo coupé (avec Turbo 11,8 W et 105 °C) ; glitchs vus par Hamza le 16/10 sur l'officielle jamais revus | 2026-10-17 | ef4712f59c |
| Super Mario Galaxy 2 `0100FD8022DAA000` | 🟢 Parfait | début de partie | 60,0 fps, 0,1-0,7 % sous 60 | NCE requis (57,4 fps en Dynarmic) ; a4212fd545 requis avec VIDS (sinon fond étoilé perdu) | 2026-10-17 | a4212fd545 |
| Mario & Luigi: Brothership `01006D0017F7A000` | 🔵 Jouable | début de partie, Mario sur la plage | 59,7 fps, 3,2 % sous 60, 8,3 W, 69 °C | cheat 60 fps porté sur la 1.0.1 + NCE requis (sinon 30 fps) ; micro-saccades côté CPU invité ; crashait au démarrage avant ef4712f59c | 2026-10-17 | ef4712f59c |
| Super Mario RPG `0100BC0018138000` | 🔵 Jouable | carte | 60,0 fps, 0 % sous 60, 4,1 W, 58 °C | vignettes de sauvegarde en bruit (sauvegardes faites sur l'officielle, pas retesté avec une sauvegarde de notre build) | 2026-10-17 | ef4712f59c |
| Zelda: Echoes of Wisdom `01008CF01BAAC000` | 🔵 Jouable | parcours de 6 min (citadelle, Gerudo, Hébra, volcan, Zora), donjon | 59,2 fps, 3,2 % sous 60 (parcours en High) ; 60,0 fps en donjon | brefs passages à 30 fps après une image en retard ; cimes noires 1-2 images à l'ouverture de la carte (1 fois sur 5) ; INI : High, VIDS, GPU et shaders asynchrones, Turbo coupé | 2026-10-17 | ef4712f59c |
| Super Mario Party Jamboree `0100965017338000` | 🟠 Menus/intro | intro, hub | 60,0 fps en intro (10,5 W, 82 °C), hub 59,9 fps | plateaux et mini-jeux jamais vus ; asserts texture format 0 et applet manette mode 1 à revoir ; INI de l'officielle, Turbo coupé | 2026-10-17 | ef4712f59c |
| Super Smash Bros. Ultimate `01006A800016E000` | ⚪ Non testé | | | copie en cours le 18/10 | | |

Seul un boot de 20 s (titre) a tourné sur l'APK installée a4212fd545 ; les observations en partie datent de ef4712f59c.
