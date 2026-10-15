# Odin 3 / Adreno 830 : par où commencer pour optimiser un jeu précis

Note locale (branche odin3, non upstream). Matériel visé : AYN Odin 3, Snapdragon 8 Elite, Adreno 830, pilote Vulkan Qualcomm stock 512.800.72 (des builds Turnip « Gen8 » existent, non testées). Jeux de référence : Zelda Echoes of Wisdom `01008CF01BAAC000` (lent), Super Mario Bros. Wonder `010015100B514000` (correct). Build utilisée : Eden Optimized v0.2.1 (flavor `genshinSpoof`). Chemins relatifs à la racine, lignes au commit `e255c20db`. Cartes par module : `src/*/AGENTS.md`.

## 0. Banc de test

- Ni Docker ni l'émulateur Android de ce Mac n'ont d'Adreno ni de pilote Qualcomm : ils ne valident que la compilation et le démarrage. Le seul banc valable est l'Odin 3 via adb (USB, ou Wi-Fi avec `adb pair`/`adb connect`), `adb` étant dans `~/Library/Android/sdk/platform-tools`.
- Build et variantes : `src/android/AGENTS.md`. Pour profiler, `relWithDebInfo` est débogable et s'installe à côté de l'officielle (suffixe de package), mais le suffixe annule le nom de package spoofé de « Optimized » : comparer aussi avec la build officielle.
- Ne jamais désinstaller la build officielle pour installer la nôtre sans sauvegarde : la clé de signature diffère et la désinstallation efface les données.
- Banc automatisé : `tools/odin3/run_variant.sh <nom> [Section.clé=valeur ...]` relance le jeu avec ces réglages par jeu, charge la sauvegarde à la manette virtuelle (`pad.sh`), fait marcher Link 60 s et écrit dans `tools/odin3/runs/<nom>/summary.txt` : fps hôte (timestamps SurfaceFlinger), part d'images lentes, puissance batterie, fréquences, températures et %CPU par thread. Il refuse d'envoyer une entrée si Eden n'a pas le focus.
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
- Contournement de pilote : `vulkan_device.cpp:522-583` pour les extensions et capacités, `Profile` (`vk_pipeline_cache.cpp:379-468`) pour les shaders. La génération Adreno n'est détectée nulle part en C++ ; seules la version du pilote (`vulkan_device.cpp:543-544`) et le nom du device sont disponibles.

## 7. Mesures Zelda EoW (2026-10-15, donjon, 60 s de marche)

- v0.2.1 officielle, réglages d'origine (turbo actif, précision GPU High) : 30,0 fps, 10,3 W, CPU à 105 °C.
- Même build sans turbo : 55,5 fps, 6,6 W. Précision GPU Low et GPU asynchrone ne changent rien (55-56 fps, 6,4-6,7 W).
- Les 8 % d'images lentes restants arrivent par salves d'exactement 12 images à 30 fps : le jeu passe 0,4 s en 30 fps après chaque image en retard.
- Build de cette branche (`relWithDebInfo`) : 58,2 fps et 2,7 % d'images lentes avec turbo ; 59,3 fps et 1,2 % sans turbo, mais 9,5 W contre 6,4 W pour la v0.2.1. Cet écart de consommation reste à expliquer (changements upstream ou perte du profil pilote liée au suffixe de package).
