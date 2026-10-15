# src/core — carte locale (branche odin3)

Cœur HLE : `System`, noyau émulé, services (nvdrv, vi…), mémoire invitée, timing, glue CPU. Chemins relatifs à la racine, lignes au commit `e255c20db`.

## Cycle de vie

- `Impl::Initialize` `src/core/core.cpp:118` : mémoire device, core timing, lit `use_asynchronous_gpu_emulation` `:138`.
- `Impl::Load` `src/core/core.cpp:314` : `InitializeKernel` `:319`, `CreateApplicationProcess` `:330`, `SetCurrentProgramID` `:354`, `SetupForApplicationProcess` `:363` (crée GPU et renderer via `VideoCore::CreateGPU` `:292`), `cpu_manager.Initialize` `:370`, `perf_stats` `:390`, puis `GameSettings::LoadOverrides` `:403` (renderer déjà créé).
- `ShutdownMainProcess` `src/core/core.cpp:414` : `Common::ADPF::Shutdown` `:416`, remise à zéro des flags par jeu.
- Le thread GPU est démarré par le frontend, pas par core (Android : `src/android/app/src/main/jni/native.cpp:340`).

## Overrides par titre

- `GameSettings::LoadOverrides` `src/core/game_settings.cpp:122-138` : `switch` sur `TitleID` (`src/core/game_settings.h:46-48`). Seul cas : Ninja Gaiden Ragebound → `use_squashed_iterated_blend` (`game_settings.cpp:127`).
- Vendor (`GetGPU` `:19-74`, Qualcomm et Turnip → `GPUVendor::Qualcomm`) et OS (`DetectEnvironment` `:114`) sont calculés mais seulement journalisés : c'est le point d'accroche naturel pour un override « titre + Qualcomm ».
- Autres listes codées en dur : `NEEDS_D24` (`src/video_core/renderer_vulkan/vk_rasterizer.h:146-150`), `FIRMWARE_REQUIRED_GAMES` (`src/frontend_common/firmware_manager.h:23-25`).
- Paramètres de lancement : `src/core/launch_params.cpp` (Parse `:23-153`, Apply `:155-203`).

## CPU et timing

- `CpuManager::Initialize` `src/core/cpu_manager.cpp:26`, threads « CPUCore_N » épinglés sur les cœurs perf `:174-177`. Boucle invitée `MultiCoreRunGuestThread` `:73` → `PhysicalCore::RunThread` `src/core/hle/kernel/physical_core.cpp:26`.
- Choix Dynarmic / NCE : `src/core/hle/kernel/k_process.cpp:1309-1328`. Détails JIT : `src/dynarmic/AGENTS.md`.
- `CoreTiming` : thread « HostTiming » `src/core/core_timing.cpp:73-77`, `GetClockTicks` `:226` (mise à l'échelle par `cpu_clock` `:228-231`, cibles 1020/1734/2040 MHz `:26-39`).
- Ordonnanceur noyau : `src/core/hle/kernel/k_scheduler.cpp` (`ScheduleImpl` `:384`, `SwitchThread` `:326`).

## Mémoire

- `Memory::Impl` `src/core/memory.cpp:47`, fastmem choisi `:53` via `Settings::IsFastmemEnabled`. `RasterizerMarkRegionCached` `:451` lit `use_reactive_flushing` `:458`. `InvalidateNCE` (SIGSEGV NCE) `:964-985`.
- SMMU / adresses device : `src/core/device_memory_manager.h:43`, implémentation `device_memory_manager.inc`.
- `HeapTracker` plafonne les mappings sur Android : `src/common/heap_tracker.cpp:15-28`.

## Chemin des commandes GPU (nvdrv → video_core)

`NVDRV::Ioctl1` `src/core/hle/service/nvdrv/nvdrv_interface.cpp:58` → `nvhost_gpu::Ioctl1` `src/core/hle/service/nvdrv/devices/nvhost_gpu.cpp:54` → `SubmitGPFIFOImpl` `:341` → `gpu.PushGPUEntries` `:373`. Mapping GPU VA : `nvhost_as_gpu::MapBufferEx` `src/core/hle/service/nvdrv/devices/nvhost_as_gpu.cpp:300`.

## Présentation, vsync, mesure

- `vi::Conductor` `src/core/hle/service/vi/conductor.cpp:25` (event « ScreenComposition », thread « VSyncThread » `:86`), `ProcessVsync` `:76`, cible ADPF `:78`.
- Composition : `HardwareComposer::ComposeLocked` `src/core/hle/service/nvnflinger/hardware_composer.cpp:49` → `nvdisp_disp0::Composite` `src/core/hle/service/nvdrv/devices/nvdisp_disp0.cpp:63` → `RequestComposite` `:90`, rapport ADPF + limitation de vitesse + perf stats `:91-94`.
- `PerfStats` : `src/core/perf_stats.h:15-24` (fps système, fps jeu, frametime, vitesse), `GetAndResetStats` `src/core/perf_stats.cpp:102-129` (lecture destructive), CSV si `record_frame_times` `:35-62`.

## Pièges

- `LoadOverrides` s'exécute après l'application des réglages par jeu : un override y écrase le choix utilisateur sans UI pour le voir.
- `LoadOverrides` (`core.cpp:403`) arrive après `CreateGPU` (`core.cpp:292`), donc après la création du device Vulkan et du `PipelineCache` : un override de `dyna_state`, `vertex_input_dynamic_state` ou `use_asynchronous_shaders` n'aurait aucun effet sur la session en cours. Seuls les réglages lus au fil de l'eau (ex. `use_squashed_iterated_blend`, lu au draw) y fonctionnent.
- Sous NCE, CNTPCT est patché : `cpu_clock` n'a probablement pas d'effet (déduction, non mesuré).
