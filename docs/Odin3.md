# Odin 3 / Adreno 830 : par où commencer pour optimiser un jeu précis

Note locale (branche odin3, non upstream). Matériel visé : AYN Odin 3, Snapdragon 8 Elite, Adreno 830, pilote Vulkan Qualcomm stock 512.800.72 (des builds Turnip « Gen8 » existent, non testées). Jeux de référence : Zelda Echoes of Wisdom `01008CF01BAAC000` (lent), Super Mario Bros. Wonder `010015100B514000` (correct). Build utilisée : Eden Optimized v0.2.1 (flavor `genshinSpoof`). Chemins relatifs à la racine, lignes au commit `e255c20db`. Cartes par module : `src/*/AGENTS.md`.

## 0. Banc de test

- Ni Docker ni l'émulateur Android de ce Mac n'ont d'Adreno ni de pilote Qualcomm : ils ne valident que la compilation et le démarrage. Le seul banc valable est l'Odin 3 via adb (USB, ou Wi-Fi avec `adb pair`/`adb connect`), `adb` étant dans `~/Library/Android/sdk/platform-tools`.
- Build et variantes : `src/android/AGENTS.md`. Pour profiler, `relWithDebInfo` est débogable et s'installe à côté de l'officielle (suffixe de package), mais le suffixe annule le nom de package spoofé de « Optimized » : comparer aussi avec la build officielle.
- Ne jamais désinstaller la build officielle pour installer la nôtre sans sauvegarde : la clé de signature diffère et la désinstallation efface les données.
- Banc automatisé : `tools/odin3/run_variant.sh <nom> [Section.clé=valeur ...]` relance le jeu avec ces réglages par jeu, charge la sauvegarde à la manette virtuelle (`pad.sh`), fait jouer `play.py` 90 s (`DUR`, `MODE=walk` pour l'aller-retour de `walk.sh`, `MODE=none` sans entrée) et écrit dans `tools/odin3/runs/<nom>/summary.txt` : fps hôte (timestamps SurfaceFlinger), part d'images lentes, puissance, fréquences, températures et %CPU par thread. Il refuse d'envoyer une entrée si Eden n'a pas le focus.
- `play.py` (Mac : ffmpeg, numpy, Pillow) joue vraiment : il suit Link à sa capuche bleue dans un flux `screenrecord` 480×270, explore en préférant les directions qui mènent à des vues nouvelles, frappe ce qui bouge près de Link, tire des flèches, lance des bombes, ferme les dialogues et l'écran de game over. Journal `play.log` et une image toutes les 10 s dans `frames/`. Ne jamais utiliser `screencap` pendant une mesure : chaque capture bloque SurfaceFlinger et fait tomber le jeu de 60 à 37 fps ; le flux garde 60 fps pour environ 0,7 W constant, donc ne comparer que des runs du même `MODE`. Un `screenrecord` qui s'arrête proprement (limite de 180 s, SIGTERM) demande au MediaProvider de scanner « - », ce qui tient un cœur prime à 4,3 GHz pendant une minute (12 à 18 W au total) : `play.py` le lance sans limite et ne le tue qu'en SIGKILL.
- Scénarios : `SCENARIO=<nom> run_variant.sh <run>` rejoue `tools/odin3/scenarios/<nom>.txt` (syntaxe en tête de `play.py`) depuis la sauvegarde nommée par sa ligne `save`, que `saves.sh push` restaure avant le lancement. Le parcours est donc le même à chaque run, et `summarize.py` donne fps, images lentes et puissance par segment (`mark`). Les sauvegardes sont dans `tools/odin3/saves/` (non versionné) : une sauvegarde 100 % téléchargée, posée devant la Citadelle (`eow-citadelle`) et devant chaque point de téléportation (`eow-gerudo`, `eow-firone`, `eow-hebra`, `eow-volcan`, `eow-zora`). `tour` enchaîne en 6 min la Citadelle, le château, le désert Gerudo avec un combat, Firone, Hébra, le volcan et La Zora ; chaque téléportation recale la route. Pour écrire un scénario, un run `AUTHOR=1` enregistre les images de référence des `check`, puis un run normal signale `DIVERGED` dans `play.log` et `route.txt` dès que la route est perdue. Le premier run d'une build compile les shaders de chaque zone (25-38 fps à la première visite) : il faut le jeter.
- Sur chargeur, la batterie reçoit ce que l'appareil laisse de l'entrée USB : `summarize.py` affiche alors entrée USB moins charge batterie. Les pertes du chargeur ne sont pas comptées, ce chiffre sert à comparer des runs, pas à les comparer aux mesures sur batterie.
- ES-DE (`org.es_de.frontend`) est le lanceur de Hamza : il ouvre le jeu par une intent VIEW sur une URI de son propre provider (`content://org.es_de.frontend.files/root/...`). `getParentDocumentUri` (`src/android/app/src/main/java/org/yuzu/yuzu_emu/utils/GameHelper.kt:243`) ne sait remonter que les URI SAF : les mises à jour et DLC posés à côté de la ROM ne sont donc pas montés, et le jeu démarre en version de base. Parade en place : le dossier des ROM Switch déclaré en SAF dans `[UI]` du `config.ini`, `Paths\external_content_dirs\1\path="content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch"` (`size=1`), monté par `mountExternalContentDirectories` à chaque lancement ; le log doit montrer « Update (...) applied successfully ».
- Dossier utilisateur : `getExternalFilesDir(null)` (`src/android/app/src/main/java/org/yuzu/yuzu_emu/utils/DirectoryInitialization.kt:42`), soit `/storage/emulated/0/Android/data/<package>/files/`. On y trouve `config/custom/<TITLEID>.ini` (réglages par jeu), `shader/<titleid>/` (caches) et `log/`.

## 1. Mesurer avant de toucher

- Overlay de l'app : fps jeu, frametime, « shaders en compilation » (`src/android/app/src/main/java/org/yuzu/yuzu_emu/fragments/EmulationFragment.kt:1525-1652`).
- Frametimes en CSV : `record_frame_times=true` dans la section `[Debugging]` de l'INI global (lu `src/frontend_common/config.cpp:304-305`, pas d'UI), fichier écrit dans `log/` à la fermeture (`src/core/perf_stats.cpp:35-62`).
- Logs : `adb logcat -s YuzuNative` (`src/common/logging.cpp:314-332`). Le démarrage logue pilote, version et extensions. Avec `gpu_log_level` et `gpu_log_driver_debug` (`src/common/settings.h:907-915`), on obtient `log/eden_gpu.log` (`src/video_core/gpu_logging/gpu_logging.cpp:50-67`), qui ne contient aucun timing GPU.
- Perfetto système : Eden n'émet aucun marqueur atrace, la trace montre donc l'ordonnancement des threads par nom (`CPUCore_0..3`, `GPU`, `VulkanWorker`, `VulkanPresent`, `HostTiming`, `VSyncThread`, workers `VkPipelineBuil…` tronqués).

```sh
adb shell perfetto -o /data/misc/perfetto-traces/eden.pftrace -t 20s sched freq idle gfx view power
adb pull /data/misc/perfetto-traces/eden.pftrace
```

  Ouvrir le fichier sur ui.perfetto.dev.
- Compteurs GPU : Eden n'a aucune requête de timestamp GPU. Il faut un outil externe : sources Perfetto `gpu.counters`/`gpu.renderstages`, Snapdragon Profiler ou Android GPU Inspector. Aucun n'a été vérifié sur l'Odin 3 ; ils exigent souvent une app débogable (`relWithDebInfo`).
- CPU : simpleperf (NDK) sur une build débogable, puisque le manifest n'a pas `profileable`.
- Couche de validation Vulkan : `renderer_debug` plus la lib téléchargée au build (`CMakeLists.txt:258`, `:285-293`). Très lente, à réserver aux bugs visuels.
- RenderDoc : code présent (`src/core/tools/renderdoc.cpp:34-39`) mais sans déclencheur sur Android.

## 2. Classer le goulot

- Saccades qui coïncident avec « shaders en compilation » > 0, et deuxième lancement plus fluide que le premier : c'est la compilation, voir §3.
- FPS bas et stable avec le thread `GPU` ou un `CPUCore_N` saturé dans la trace : c'est l'émulation côté CPU, voir §4.
- FPS bas, threads CPU peu chargés, `VulkanWorker`/`VulkanPresent` en attente et fréquence GPU au maximum : c'est le GPU hôte, voir §5.
- Toujours comparer EoW et Mario Wonder dans la même configuration : aucun des deux title IDs n'apparaît dans le code, l'écart vient donc de la charge propre à chaque jeu.

## 3. Compilation des shaders et pipelines

Ces réglages se changent par jeu, sans toucher au code, depuis Propriétés du jeu → Réglages dans l'app :

- `pipeline_worker_count` : 2 par défaut, plafonné à `min(cœurs-1, 8)` (`src/video_core/renderer_vulkan/vk_pipeline_cache.cpp:306-320`), soit jusqu'à 7 sur un 8 Elite. Il n'accélère que `vkCreateGraphicsPipelines` : la traduction Maxwell → SPIR-V reste sur le thread `GPU` (`vk_pipeline_cache.cpp:814-901`).
- `use_asynchronous_shaders` saute les draws tant que leur pipeline n'est pas prêt (`vk_pipeline_cache.cpp:783-798`). Moins de saccades, mais des artefacts passagers.
- `dyna_state` (EDS1 à EDS3) et `vertex_input_dynamic_state` sont désactivés par défaut sur tout Android (`src/common/settings.h:684-709`). Les activer réduit le nombre de variantes de pipelines. Le risque est un bug du pilote, ce qui explique le défaut ; c'est le premier essai à faire sur le pilote 8xx. Après un changement, une partie du cache disque est ignorée (`vk_pipeline_cache.cpp:664-690`).
- Laisser `use_disk_shader_cache` et `use_vulkan_driver_pipeline_cache` actifs. Ces deux réglages et l'async ne sont lus qu'au démarrage du jeu (`vk_pipeline_cache.cpp:354-355`).

Côté code, deux pistes :

- Faire la traduction sur les workers quand l'async est actif.
- Mettre en cache le SPIR-V : aujourd'hui chaque boot retraduit tout le cache disque (`vk_pipeline_cache.cpp:604`).

## 4. Émulation côté CPU

- Vérifier d'abord que NCE est actif. `adb shell getconf PAGE_SIZE` doit renvoyer 4096, sinon fastmem et NCE sont coupés (`src/common/settings.cpp:181-211`). Le backend actif est donné par `getCpuBackend` (`src/android/app/src/main/jni/native.cpp:1018`).
- Sous Dynarmic arm64, les options « Unsafe » n'ont aucun effet. Seul compte le contrôle de bornes fastmem (Auto vs Accurate), voir `src/dynarmic/AGENTS.md`.
- Thread `GPU` saturé : les macros GPU passent par l'interpréteur sur arm64 (`src/video_core/macro.cpp:1340`), puis viennent `DmaPusher`, le texture cache et le buffer cache (`src/video_core/AGENTS.md`). Réglages à essayer : `gpu_accuracy` (Low par défaut sur Android), `dma_accuracy`, `use_reactive_flushing`, `sync_memory_operations`.
- Le profil de performance AYN actif pendant les mesures impose des planchers même au repos : CPU 0-5 ≥ 2,0 GHz, CPU 6-7 ≥ 2,25 GHz, GPU ≥ 660 MHz (`min_pwrlevel=6`). Toujours mesurer dans le même mode ; un mode plus économe est la prochaine piste batterie. Les threads chauds sont déjà épinglés sur les gros cœurs, avec indications ADPF (`src/common/thread.cpp:542-549`).

## 5. GPU hôte (Adreno 830)

- `resolution_setup` à 0.75x ou 0.5x, avec `scaling_filter` SGSR (upscaler Snapdragon, `src/video_core/renderer_vulkan/present/sgsr.cpp:24-45`).
- `renderer_force_max_clock` appelle `adrenotools_set_turbo` (`src/video_core/renderer_vulkan/vk_turbo_mode.cpp`), qui coupe le DCVS et fige le GPU à 1100 MHz pour tout l'appareil, même après la mort du processus. En v0.2.1 le thread turbo tournait en boucle (98 % d'un gros cœur) et verrouillait EoW à 30 fps. Corrigé sur cette branche : rafraîchissement toutes les secondes, relâché quand le jeu est en pause, et remis à zéro au démarrage quand le réglage est coupé. `tools/odin3/kgslturbo 0` débloque un GPU resté figé.
- Les contournements Qualcomm ont un coût permanent dans les shaders :
  - shuffle de subgroup transformé en boucle ;
  - Int64 émulé ;
  - formats scaled émulés.

  Ils sont posés dans `src/video_core/vulkan_common/vulkan_device.cpp:522-539` et `vk_pipeline_cache.cpp:453-462`. Le seuil de version 512.672.0 (`vk_pipeline_cache.cpp:459-461`) montre comment conditionner un contournement à la version du pilote : à retester un par un sur le pilote 8xx.
- Rendu en tuiles : Android vide le travail tous les 512 draws (`src/video_core/renderer_vulkan/vk_rasterizer.cpp:984-990`). C'est un paramètre à mesurer sur l'Adreno 830.
- `fix_bloom_effects` cite EoW dans l'UI (`src/android/app/src/main/res/values/strings.xml:601`). C'est un correctif visuel qui multiplie les échantillons par 0.98 (`src/shader_recompiler/backend/spirv/emit_spirv_image.cpp:593-597`), pas un levier de performance.
- Patch BCn : `patch_old_qcom_drivers=false` écrase la détection automatique (`vulkan_device.cpp:549-555`). L'effet sur un Adreno 830 n'est pas vérifié.

## 6. Où brancher un correctif propre à un jeu

- Sans code : l'INI par jeu, par exemple `config/custom/01008CF01BAAC000.ini`, section `[Renderer]` avec `use_asynchronous_shaders\use_global=false` puis `use_asynchronous_shaders=true`. Les sections viennent de `TranslateCategory` (`src/common/settings.cpp:238-303`).
- Défaut imposé par Eden pour un titre : `GameSettings::LoadOverrides` (`src/core/game_settings.cpp:122-138`). Ajouter l'ID dans `TitleID` (`src/core/game_settings.h:46-48`) et conditionner sur `GPUVendor::Qualcomm`, déjà calculé (`game_settings.cpp:19-74`).
  - Piège : ce point s'exécute après la création du device et du cache de pipelines (`src/core/core.cpp:292` puis `:403`). Il ne sert qu'aux réglages lus au fil de l'eau.
  - Pour `dyna_state` ou l'async, il faudrait appliquer l'override avant `CreateGPU`.
- Jeu plafonné à 30 fps par lui-même : un cheat Atmosphere dans `files/load/<TID>/<dossier>/cheats/<16 premiers hex du build ID de main>.txt` (`tools/odin3/cheats/`). Eden exécute toutes les entrées du fichier. Pour porter un cheat vers un autre build ID, dumper main par le stub GDB (`tools/odin3/gdbmem.py`) : `dump_nso` écrit des fichiers que adb ne peut pas lire. Vérifier ensuite que le jeu garde sa vitesse à 60 fps.
- Contournement de pilote : `vulkan_device.cpp:522-583` pour les extensions et capacités, `Profile` (`vk_pipeline_cache.cpp:379-468`) pour les shaders. La génération Adreno n'est détectée nulle part en C++ ; seules la version du pilote (`vulkan_device.cpp:543-544`) et le nom du device sont disponibles.

## 7. Mesures Zelda EoW (2026-10-15, donjon, 60 s de marche)

- v0.2.1 officielle, réglages d'origine (turbo actif, précision GPU High) : 30,0 fps, 10,3 W, CPU à 105 °C.
- Même build sans turbo : 55,5 fps, 6,6 W. Précision GPU Low et GPU asynchrone ne changent rien (55-56 fps, 6,4-6,7 W).
- Les 8 % d'images lentes restants arrivent par salves d'exactement 12 images à 30 fps. C'est le jeu qui le décide : après une image en retard, il passe son swap interval de 1 à 2 pendant 24 vsyncs (0,4 s).
- Build de cette branche (`relWithDebInfo`) : 58,2 fps et 2,7 % d'images lentes avec turbo ; 59,3 fps et 1,2 % sans turbo, mais 9,5 W contre 6,4 W pour la v0.2.1.

Le même jour, avec `play.py` pendant 90 s sur chargeur et la build `relWithDebInfo` (chiffres comparables entre eux seulement) :

- HEAD : 60,0 fps, 0 % d'images lentes, 6,66 W sur deux runs identiques. Le CPU 0 tourne à 3,4 GHz pour 40 % d'occupation.
- Sessions ADPF gardées mais sans rapport de durée : 59,3-59,5 fps, 1,0-1,3 %, 5,64-5,81 W. Sans ADPF du tout : 59,2-59,7 fps, 0,4-1,3 %, 5,53-5,54 W.
- Cause : `ReportFrameInterval` rapportait l'intervalle entre deux vsyncs comme durée de travail. Cet intervalle vaut la cible par construction, et le double pendant les salves à 30 fps : il ne descend jamais en dessous, donc le service de hints relève sans fin le plancher des CPU.
- Rapport retiré (`src/common/adpf.h`) : 59,5-59,8 fps, 0,5-0,8 %, 5,31-5,57 W. Soit environ −1,2 W (−18 %) pour deux ou trois salves de 12 images de plus toutes les 90 s.
- `async_presentation`, actif par défaut depuis #4509, n'y est pour rien : coupé, 59,5 fps et 6,92 W.
- GPU synchrone (`use_asynchronous_gpu_emulation=false`, valeur de l'INI global de la v0.2.1) : 30 à 40 fps sur cette branche (30,0 fps et 4,38 W avec tous les réglages de la v0.2.1). Ce n'est pas une régression : dans la même salle, le merge-base `ee188168c1` donne 60 fps en asynchrone et 30 fps en synchrone, et le parent de #4254 aussi. C'est le jeu qui choisit un swap interval de 2, au seuil de son heuristique ; `gpu_accuracy=0`, `System.fast_gpu_time=2` et une libération plus tôt du framebuffer n'y changent rien. Les 55 fps de la v0.2.1 venaient d'un autre état de la sauvegarde.
- L'INI par jeu contient `gpu_accuracy=1` : Medium en v0.2.1, mais High depuis que #4182 a renuméroté l'enum. `[Renderer] fast_gpu_time` est ignoré depuis #4254, qui l'a déplacé dans `[System]`.
- Le GPU reste à 660 MHz dans tous ces runs : son occupation (35-40 % contre 22 %) suit le nombre d'images et le GPU asynchrone, pas l'écart de puissance.
- La v0.2.1 n'a pas pu être remesurée : dans la salle de l'escalier du donjon, les builds de son époque affichent un monde noir (HUD seul, 30 fps), même avec le cache de shaders écarté ou le GPU asynchrone forcé. Le correctif se trouve entre `ee188168c1` et `5ec94b1971`. Le profil pilote lié au suffixe de package n'a donc pas été mesuré.
