# src/video_core — carte locale (branche odin3)

Émulation du GPU Maxwell (Tegra X1) et rendu hôte (Vulkan, OpenGL, Null). Sur Android seul Vulkan compte. Chemins relatifs à la racine du dépôt, lignes au commit `e255c20db` : revérifier avant de citer.

## Flux principal

1. Création : `VideoCore::CreateGPU` `src/video_core/video_core.cpp:49`, renderer choisi par `CreateRenderer` `:26` (Vulkan `:36`).
2. Soumission : nvdrv (`src/core/hle/service/nvdrv/devices/nvhost_gpu.cpp:373`) → `GPU::Impl::PushGPUEntries` `src/video_core/gpu.cpp:205` → `ThreadManager::SubmitList` `src/video_core/gpu_thread.cpp:65`.
3. Thread « GPU » : `ThreadManager::StartThread` `src/video_core/gpu_thread.cpp:28`, boucle `:43-52`. En mode synchrone `PushCommand` bloque le CPU (`:98-101`).
4. Pushbuffer : `src/video_core/control/scheduler.cpp:16` → `DmaPusher::DispatchCalls` `src/video_core/dma_pusher.cpp:31` → `CallMethod` `:175`. Lecture safe/unsafe selon `dma_accuracy` `:86`.
5. Engines : `Maxwell3D::CallMethod` `src/video_core/engines/maxwell_3d.cpp:388` → `DrawManager::ProcessDraw` `src/video_core/engines/draw_manager.cpp:276` → `rasterizer->Draw` `:280`. Compute : `src/video_core/engines/kepler_compute.cpp:103`. DMA : `src/video_core/engines/maxwell_dma.cpp:60`. Macros HLE/interpréteur : `MacroEngine::Execute` `src/video_core/macro.cpp:1340` (JIT macro x86_64 seulement `:715`, donc interpréteur sur Android).
6. Draw Vulkan : `RasterizerVulkan::Draw` `src/video_core/renderer_vulkan/vk_rasterizer.cpp:262` → `PrepareDraw` `:236` → `pipeline_cache.CurrentGraphicsPipeline()` `:244` (nullptr = draw sauté `:245-247`) → `GraphicsPipeline::ConfigureImpl` `src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp:352` (texture cache `:452`, buffer cache `:516-517`).
7. Enregistrement/soumission : thread « VulkanWorker » `src/video_core/renderer_vulkan/vk_scheduler.cpp:52` (`WorkerThread` `:270`, `SubmitExecution` `:342`, `vkQueueSubmit` `:364`). Android flush tous les 512 draws au lieu de 4096 (`vk_rasterizer.cpp:984-990`).
8. Présentation : `RendererVulkan::Composite` `src/video_core/renderer_vulkan/renderer_vulkan.cpp:204` → `TickFrame` `vk_rasterizer.cpp:893` ; `PresentManager::Present` `src/video_core/renderer_vulkan/vk_present_manager.cpp:189`, thread « VulkanPresent » si `async_presentation` (`:132`).

## Device Vulkan et spécificités Adreno / Qualcomm

Tout est dans le constructeur `Device::Device` `src/video_core/vulkan_common/vulkan_device.cpp:451`.

- Détection : booléens locaux `is_qualcomm` (stock, `VK_DRIVER_ID_QUALCOMM_PROPRIETARY`) `:470` et `is_turnip` (`VK_DRIVER_ID_MESA_TURNIP`) `:471`. Pas de helper `IsQualcomm()` ; ailleurs on compare `GetDriverID()` (`src/video_core/vulkan_common/vulkan_device.h:316-318`). `IsTiler()` `vulkan_device.h:325-335`.
- Version Qualcomm : `major=(v>>24)<<2`, `minor=(v>>12)&0xFFF` (`vulkan_device.cpp:543-544`).
- La génération Adreno (6xx/7xx/8xx) n'est détectée nulle part en C++ ; seul le Kotlin parse « Adreno (TM) NNN » pour l'UI des pilotes.
- Bloc Qualcomm stock `vulkan_device.cpp:522-539` : `must_emulate_scaled_formats`, `has_broken_descriptor_aliasing`, retrait de `VK_EXT_color_write_enable`, `VK_KHR_shader_atomic_int64` (+ `shaderInt64=false`, donc lowering Int64 côté shaders) et `VK_KHR_workgroup_memory_explicit_layout`.
- Patch BCn via libadrenotools `vulkan_device.cpp:541-582` (Android arm64), formats forcés par `OverrideBcnFormats` `:320-345`. Piège : `patch_old_qcom_drivers` vaut false par défaut (`src/common/settings.h:722`) et écrase la détection auto `api>=28` (`vulkan_device.cpp:549-555`), donc le patch est désactivé sauf toggle debug ; ce qu'`adrenotools_get_bcn_type` renvoie pour un Adreno 830 n'est pas vérifié.
- Turnip et Qualcomm : `maxVertexInputBindings = 32` `vulkan_device.cpp:659-662`. Turnip seul : pas de timeline semaphore `:988-992`.
- `dyna_state` retire EDS1/2/3 `vulkan_device.cpp:664-697`, `vertex_input_dynamic_state` `:700-702` ; descriptor indexing forcé off `:705-722`.
- ASTC natif Adreno : `is_optimal_astc_supported` `vulkan_device.cpp:504`, donc le décodeur compute ASTC est sauté (`src/video_core/renderer_vulkan/vk_texture_cache.cpp:151-153`).
- Horloges : `ShouldBoostClocks` `vulkan_device.cpp:967` ; `renderer_force_max_clock` → TurboMode `renderer_vulkan.cpp:184` → `adrenotools_set_turbo` (`src/video_core/renderer_vulkan/vk_turbo_mode.cpp:157-160`).
- Code mort : `has_broken_parallel_compiling` (`vulkan_device.h:1227`) n'est jamais assigné.
- Ailleurs : `vk_buffer_cache.cpp:362-366` (limite storage buffers dynamiques Qualcomm/ARM), `vk_query_cache.cpp:238-243` et `:1522-1525` (pas de presync ni de conditional rendering hôte sur tilers mobiles), `vk_compute_pipeline.cpp:113-125` (« Adreno rejected compute shader »), descriptor buffer 2 MiB sur tiler `vk_descriptor_buffer.cpp:29`.
- Seule extension QCOM utilisée : `VK_QCOM_filter_cubic_weights` (`vulkan_device.h:118`, présentation `src/video_core/renderer_vulkan/present/filters.cpp:48-71`). SGSR (upscaler mobile) : `src/video_core/renderer_vulkan/present/sgsr.cpp:24-45`, choisi dans `src/video_core/renderer_vulkan/present/layer.cpp:74-77`.
- Profil shader dépendant du pilote Qualcomm : voir `src/shader_recompiler/AGENTS.md` (remplis dans `vk_pipeline_cache.cpp:453-462`).
- Pilote custom (adrenotools) chargé côté JNI : voir `src/android/AGENTS.md`.

## Cache de pipelines et compilation

Fichier central `src/video_core/renderer_vulkan/vk_pipeline_cache.cpp`.

- Fichiers : `<user>/shader/<titleid 016x>/vulkan.bin` (code invité + environnement, pas de SPIR-V) et `vulkan_pipelines.bin` (`VkPipelineCache` du pilote), créés `:616-629`. `CACHE_VERSION = 18` `:66`.
- Lookup : `CurrentGraphicsPipeline` `:565` (chemin rapide par liste de transitions), sinon `CurrentGraphicsPipelineSlowPath` `:767` → `CreateGraphicsPipeline` `:926`.
- Sur un miss, traduction Maxwell → IR → SPIR-V et `vkCreateShaderModule` se font de façon synchrone sur le thread GPU (`:814-901`) ; seul `vkCreateGraphicsPipelines` part sur les workers (`:902`, `:932`). Sérialisation disque `:936-944`, flush du cache pilote `QueueVulkanPipelineCacheFlush` `:738`.
- Shaders asynchrones : `BuiltPipeline` `:783-798` renvoie nullptr (draw sauté) sauf si index/vertex count ≤ 6. Sinon, attente sur le thread VulkanWorker (`vk_graphics_pipeline.cpp:599-605`). Le compute attend toujours.
- Shaders « HasBrokenPattern » sautés sur tout pilote non NVIDIA (`vk_pipeline_cache.cpp:841-843`).
- Workers : pool « VkPipelineBuilder » `:356-357`, taille `GetTotalPipelineWorkers` `:306-320` = `min(hw-1, clamp(pipeline_worker_count, 2, 8))` sur Android (défaut 2). Thread « VkPipelineSerialization » `:358`.
- Préchauffage au boot : `LoadDiskResources` `:604` rejoue tout le cache disque (traduction + compilation complètes) sur les workers, entrées incompatibles avec l'état dynamique courant sautées `:664-690`. Appelé avant `Run` sur Android (`src/android/app/src/main/jni/native.cpp:413-418`).
- `use_asynchronous_shaders` et `use_vulkan_driver_pipeline_cache` sont lus une fois à la construction (`vk_pipeline_cache.cpp:354-355`) : changer ces réglages exige un redémarrage du jeu.
- Compteur « shaders en compilation » : `src/video_core/shader_notify.cpp:15-32`.

## Réglages qui pilotent ce module

Tous dans `src/common/settings.h` : `gpu_accuracy` `:503` (Low sur Android), `dma_accuracy` `:517`, `gpu_fence_behavior` `:525`, `vram_usage_mode` `:535`, `accelerate_astc` `:551`, `astc_recompression` `:566`, `sync_memory_operations` `:572`, `renderer_force_max_clock` `:580`, `use_disk_shader_cache` `:583`, `use_vulkan_driver_pipeline_cache` `:586`, `use_reactive_flushing` `:596` (false sur Android), `gpu_clock` (clé ini `fast_gpu_time`) `:627`, `async_presentation` `:646`, `fix_bloom_effects` `:654`, `use_asynchronous_shaders` `:660`, `dyna_state` `:684`, `vertex_input_dynamic_state` `:703`, `resolution_setup` `:348`, `vsync_mode` `:351`, `scaling_filter` `:359`, `frame_gen` `:420`. Aucun n'a de défaut spécifique Qualcomm : les défauts Android valent pour tous les GPU.

## Points chauds perf

- Thread GPU : traduction de shaders sur miss, `DmaPusher`, macros interprétées, `texture_cache.h` (`UpdateRenderTargets` `src/video_core/texture_cache/texture_cache.h:462`, `FindImage` `:1214`, `JoinImages` `:1541`), `buffer_cache.h` (`UpdateGraphicsBuffers` `src/video_core/buffer_cache/buffer_cache.h:347`).
- Nombre de variantes de pipelines : EDS désactivé par défaut sur Android ⇒ plus d'état statique dans la clé ⇒ plus de pipelines.
- Swapchain : sur Android API ≥ 30 `frame_pacing_mode` est ignoré (`src/video_core/renderer_vulkan/vk_swapchain.cpp:202-210`) ; choix du present mode `:44-91`.
- `gpu_logging` (`src/video_core/gpu_logging/`) : journal CPU uniquement, aucun timing GPU ; `QualcommDebugger` est un stub.

## Pièges

- Il n'existe aucune requête de timestamp GPU hôte (`vkCmdWriteTimestamp` absent) : mesurer le GPU passe par des outils externes (voir `docs/Odin3.md`).
- `HasBrokenParallelShaderCompiling` est toujours faux ; ne pas compter dessus pour un contournement.
- Modifier la clé de pipeline ou `CACHE_VERSION` invalide les caches utilisateurs.
