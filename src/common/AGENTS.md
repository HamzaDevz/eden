# src/common — carte locale (branche odin3)

Utilitaires partagés. Pour la perf, trois morceaux comptent : le système de réglages, le placement des threads (ADPF) et la mémoire hôte / fastmem. Chemins relatifs à la racine, lignes au commit `e255c20db`.

## Réglages

- Déclarations : `struct Values` `src/common/settings.h:137`, instance `src/common/settings.cpp:83`. Doc upstream : `docs/Settings.md`.
- `Setting<T>` (global uniquement) `src/common/settings_setting.h:28` ; `SwitchableSetting<T>` (global + valeur par jeu) `:291`, bascule `use_global` `:435`, `GetValue` `:368`.
- Enums : macro `ENUM` `src/common/settings_enums.h:77`, valeurs `:132-161`. Catégories, Specialization, Linkage : `src/common/settings_common.h:17-84`.
- Défauts Android posés par `#ifdef __ANDROID__` directement dans `settings.h` : `gpu_accuracy` Low `:503`, `use_reactive_flushing` false `:596`, `async_presentation` true `:646`, `dyna_state` Disabled `:684`, `vertex_input_dynamic_state` false `:703`, `use_docked_mode` Handheld `:764`.
- Réglages propres à Android (`driver_path`, overlay, `pipeline_worker_count`) : `src/android/app/src/main/jni/android_settings.h`, voir `src/android/AGENTS.md`.
- Helpers : `UpdateGPUAccuracy` `src/common/settings.cpp:153`, `IsGPULevelHigh` `:157`, `IsFastmemEnabled` `:181-195`, `SetNceEnabled` `:199-211`, `SetCurrentProgramID` `:219`, `RestoreGlobalState` `:399-411`.

### Mécanisme par jeu

- Seuls les `SwitchableSetting` sont lus dans un INI par jeu (`ReadSettingGeneric` `src/frontend_common/config.cpp:909-932`, clé `<nom>\use_global`). Un `Setting` ne peut pas être surchargé par jeu.
- Fichier : `<ConfigDir>/custom/<TITLEID en %016X>.ini` (`src/frontend_common/config.cpp:41-42`).
- Priorité, du plus faible au plus fort : défaut compilé → INI global → INI par jeu → paramètres de lancement (`src/core/launch_params.cpp:155-203`) → `GameSettings::LoadOverrides` (`src/core/game_settings.cpp:122`) → quirks du device Vulkan (`src/video_core/vulkan_common/vulkan_device.cpp:451`).
- Ajouter un réglage par jeu : le déclarer `SwitchableSetting` dans `settings.h`, l'exposer côté Android (clé dans `src/android/app/src/main/java/org/yuzu/yuzu_emu/features/settings/model/`), et le lire avec `GetValue()` au bon moment (certains sont lus une seule fois, ex. `src/video_core/renderer_vulkan/vk_pipeline_cache.cpp:354-355`).

## Threads et ADPF

- `SetCurrentThreadToPerformanceCores` `src/common/thread.cpp:542-549` (session ADPF Render si dispo, sinon affinité), efficacité `:552`, arrière-plan `:562`. Topologie lue dans sysfs `:254-256`, `sched_setaffinity` `:341`.
- Appelants : `src/core/cpu_manager.cpp:177`, `src/core/core_timing.cpp:77`, `src/video_core/gpu_thread.cpp:33`, `src/video_core/renderer_vulkan/vk_scheduler.cpp:273`, `src/video_core/renderer_vulkan/vk_present_manager.cpp:333`.
- ADPF : `src/common/adpf.cpp` charge `APerformanceHint_*` dynamiquement (`:54-87`), sessions Render et Background, cible par défaut 16.67 ms (`:24`), aucun rapport de durée réelle (voir `docs/Odin3.md` §7).
- Pool de workers générique : `src/common/thread_worker.h:26-131` (placement Background ⇒ priorité basse).
- Piège : les noms de threads de plus de 15 caractères sont tronqués sous Linux (`src/common/thread.cpp:517-524`), ex. « VkPipelineBuilder » dans une trace.

## Mémoire hôte et journal

- `src/common/host_memory.cpp:423` : base virtuelle gardée au-dessus de 36 bits pour Qualcomm.
- `src/common/heap_tracker.cpp:15-28` : plafond de mappings sur Android.
- Logcat : tag « YuzuNative » (`src/common/logging.cpp:314-332`).
